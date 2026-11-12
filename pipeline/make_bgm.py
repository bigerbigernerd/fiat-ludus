#!/usr/bin/env python3
"""Generate loopable game background music with YuE2 (m-a-p/YuE2-3B).

    make_bgm.py --prompt "<style>" --seconds 75 --out out.ogg [--seed N]
                [--lyrics FILE] [--instrumental]

Run it with the YuE2 environment's Python inside tools/gpu_run.sh. One call
loads the model once, generates one piece and exits.

--instrumental follows the official yue2-music "instrumental" recipe: YuE2 first
writes a melody-and-chord score (ABC) from section tags only, the Vocal melody is
moved to the instrumental voice, and the revised score is rendered with no sung
words. Before rendering, whole sections are dropped from the end of the score so
its nominal length fits --seconds.

Without --instrumental the request goes to YuE2 unchanged: --lyrics is sung
(section tags only, or empty, are allowed; neither guarantees an instrumental).

Hardware is chosen automatically (override with --precision / --low-vram): BF16 on
GPUs with native BF16 (Ampere and newer), FP16 plus YuE2's official low_vram mode on
smaller or older cards (tested on an RTX 2080 Ti 11 GB: peak about 5.9-6.5 GB).

Afterwards the script measures loudness, head/tail silence and (unless
--no-separation) the vocal energy left in the mix using Demucs htdemucs on the
CPU. It writes a crossfaded loop normalised to --lufs, encodes it to the format
of --out (.ogg/.mp3/.wav/.flac) plus an MP3 sibling, and prints one JSON line.
Exit codes: 0 ok, 1 generated but needs review (truncated), 2 failure.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

SR = 48000
SEMANTIC_TOKENS_PER_SECOND = 25  # measured: 1659 semantic tokens -> 66.32 s of audio
SHORT_TAGS = "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Outro]\n"  # the official recipe's default
LONG_TAGS = "[Intro]\n\n[Verse]\n\n[Chorus]\n\n[Interlude]\n\n[Verse]\n\n[Chorus]\n\n[Outro]\n"


def default_tags(seconds: float) -> str:
    """Over-plan a little; trim_score() then cuts whole sections to fit."""
    return SHORT_TAGS if seconds <= 45 else LONG_TAGS
NO_VOICE = ("no vocals", "no singing", "no choir", "no spoken words")


def runtime_root() -> Path:
    return Path(os.environ.get("FORGE_RUNTIME", Path.home() / "forge-runtime"))


def yue2_repo() -> Path:
    return Path(os.environ.get("YUE2_REPO", runtime_root() / "repos" / "YuE2"))


def skill_scripts() -> Path:
    path = yue2_repo() / "skills" / "yue2-music" / "instrumental" / "scripts"
    if not (path / "instrumentalize.py").is_file():
        raise FileNotFoundError(f"YuE2 instrumental helpers not found under {path}; set FORGE_RUNTIME or YUE2_REPO")
    return path


def log(msg: str) -> None:
    print(f"[make_bgm] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------- score

def instrumental_style(prompt: str) -> str:
    """Same normalisation as the official instrumental recipe's prepare step."""
    style = prompt.strip().rstrip(".,") or "Expressive instrumental music"
    if not re.match(r"^instrumental\b", style, re.I):
        style = "Instrumental, " + style
    for condition in NO_VOICE:
        if condition not in style.lower():
            style += ", " + condition
    return style + "."


def trim_score(abc: str, seconds: float, slack: float = 1.0):
    """Keep a prefix of whole sections whose nominal length reaches the target.

    Works on note events (not text): notes and chords after the cut are removed,
    a note sounding across the cut is shortened. Returns (abc, report).
    """
    from abc_tools import parse_abc
    from compile_score import compile_events
    from instrumentalize import section_starts

    score = parse_abc(abc)
    vocal, ins = score.voices["Vocal"], score.voices["Ins"]
    if vocal.notes:
        raise ValueError("trim_score expects an instrumental score (no Vocal notes)")
    sections = section_starts(abc, score)
    bars, spans = [], []
    for start, _, (n, d) in vocal.bars:
        end = start + Fraction(4 * n, d)
        key = next(k for t, k in reversed(vocal.keys) if t <= start)
        bar = {"meter": f"{n}/{d}", "key": key}
        if start in sections:
            bar["section"] = sections[start]
            spans.append([sections[start], start, end])
        elif spans:
            spans[-1][2] = end
        else:
            spans.append([None, start, end])
        bars.append((start, end, bar))
    sec_per_quarter = 60 / score.bpm
    total = float(bars[-1][1]) * sec_per_quarter
    # Keep whole sections until the target is reached (the audio loop is cut from
    # the result, so overshooting by up to one section is fine; undershooting is not).
    kept = []
    for label, start, end in spans:
        kept.append((label, start, end))
        if float(end) * sec_per_quarter >= seconds * slack:
            break
    cut = kept[-1][2]
    report = {"bpm": score.bpm, "nominal_seconds_before": round(total, 2),
              "sections_before": [s[0] for s in spans], "sections_kept": [s[0] for s in kept],
              "nominal_seconds_after": round(float(cut) * sec_per_quarter, 2)}
    if cut == bars[-1][1]:
        report["trimmed"] = False
        return abc, report
    notes = []
    for start, pitch, dur in ins.notes:
        if start >= cut:
            continue
        notes.append([str(start), str(min(dur, cut - start)), pitch])
    chords = []
    for when, symbol in vocal.chords:
        if when < cut and not (chords and chords[-1][1] == symbol):
            chords.append([str(when), symbol])
    events = {"bpm": score.bpm, "key": vocal.keys[0][1],
              "bars": [b for s, e, b in bars if e <= cut], "notes": notes, "chords": chords}
    text, _ = compile_events(events)
    report["trimmed"] = True
    return text, report


# --------------------------------------------------------------------- audio

def frame_db(x, sr=SR, win=0.02):
    import numpy as np
    mono = x.mean(axis=1) if x.ndim == 2 else x
    n = int(sr * win)
    frames = len(mono) // n
    rms = np.sqrt(np.mean(mono[: frames * n].reshape(frames, n).astype(np.float64) ** 2, axis=1) + 1e-20)
    return 20 * np.log10(rms), win


def silence_edges(x, threshold_db=-50.0):
    """Seconds of near-silence at the head and tail (20 ms frames below threshold dBFS)."""
    import numpy as np
    db, win = frame_db(x)
    loud = np.nonzero(db > threshold_db)[0]
    if len(loud) == 0:
        return len(db) * win, len(db) * win
    return loud[0] * win, (len(db) - 1 - loud[-1]) * win


def loudness(x, sr=SR):
    import pyloudnorm as pyln
    return float(pyln.Meter(sr).integrated_loudness(x))


def true_peak_db(x, sr=SR):
    import numpy as np
    from scipy.signal import resample_poly
    up = resample_poly(x, 4, 1, axis=0)
    return float(20 * np.log10(np.max(np.abs(up)) + 1e-12))


def limit(x, ceiling_db=-1.0, sr=SR, attack=0.005, release=0.08):
    """Simple look-ahead peak limiter on 4x-oversampled peaks; returns limited audio."""
    import numpy as np
    from scipy.ndimage import minimum_filter1d
    from scipy.signal import resample_poly
    ceiling = 10 ** (ceiling_db / 20)
    peak = np.abs(resample_poly(x, 4, 1, axis=0)).max(axis=1).reshape(-1, 4).max(axis=1)[: len(x)]
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-12))
    look = max(1, int(attack * sr))
    gain = minimum_filter1d(need, size=2 * look + 1, mode="nearest")
    coef = np.exp(-1.0 / (release * sr))
    out = np.empty_like(gain)
    g = 1.0
    for i, target in enumerate(gain):  # fast attack (already looked ahead), slow release
        g = target if target < g else target + (g - target) * coef
        out[i] = g
    return x * out[:, None]


def band_features(x, sr=SR, hop=0.05):
    """Log energy in 24 log-spaced bands per hop; used to compare loop seams."""
    import numpy as np
    mono = x.mean(axis=1)
    n = 4096
    h = int(sr * hop)
    frames = 1 + (len(mono) - n) // h
    idx = np.arange(n)[None, :] + h * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(mono[idx] * np.hanning(n), axis=1)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / sr)
    edges = np.geomspace(60, 16000, 25)
    bands = np.stack([spec[:, (freqs >= a) & (freqs < b)].sum(axis=1) for a, b in zip(edges[:-1], edges[1:])], axis=1)
    return np.log10(bands + 1e-10), hop


def choose_loop(x, seconds, bpm, beats_per_bar, xfade):
    """Pick start (first non-silent sound) and a loop length that is a whole number
    of score bars, close to `seconds`, whose seam best resembles the loop start."""
    import numpy as np
    head, tail = silence_edges(x)
    start = max(0.0, head - 0.01)
    usable_end = len(x) / SR - tail
    bar = 60.0 / bpm * beats_per_bar
    feats, hop = band_features(x)
    probe = int(2.0 / hop)
    a0 = int(start / hop)
    ref = feats[a0:a0 + probe].ravel()
    best = None
    max_len = usable_end - start - xfade
    for nbars in range(1, 400):
        length = nbars * bar
        if length > max_len or length > seconds * 1.15:
            break
        if length < seconds * 0.6:
            continue
        b0 = int((start + length) / hop)
        cand = feats[b0:b0 + probe].ravel()
        if len(cand) != len(ref):
            continue
        sim = float(np.corrcoef(ref, cand)[0, 1])
        score = sim - 0.5 * abs(length - seconds) / seconds
        if best is None or score > best[0]:
            best = (score, nbars, length, sim)
    if best is None:  # piece shorter than requested: loop what exists
        length = max(bar, np.floor(max_len / bar) * bar) if max_len > bar else max_len
        return {"start": start, "length": float(length), "bars": int(round(length / bar)),
                "seam_similarity": None, "bar_seconds": bar, "fallback": True}
    return {"start": start, "length": best[2], "bars": best[1], "seam_similarity": round(best[3], 3),
            "bar_seconds": bar, "fallback": False}


def crossfade_loop(x, start, length, xfade):
    """Loop body [start, start+length); the audio that follows the loop end is
    equal-power crossfaded into the loop head so repetition is seamless."""
    import numpy as np
    s, n, f = int(round(start * SR)), int(round(length * SR)), int(round(xfade * SR))
    body = x[s:s + n].copy()
    after = x[s + n:s + n + f]
    f = min(f, len(after), n // 2)
    t = np.linspace(0, np.pi / 2, f)[:, None]
    body[:f] = body[:f] * np.sin(t) + after[:f] * np.cos(t)
    return body


def encode(wav: Path, out: Path):
    ffmpeg = shutil.which("ffmpeg") or str(Path(sys.executable).parent / "ffmpeg")
    ext = out.suffix.lower()
    codec = {".ogg": ["-c:a", "libvorbis", "-q:a", "4"],        # ~128 kbit/s VBR
             ".mp3": ["-c:a", "libmp3lame", "-b:a", "160k"],
             ".flac": ["-c:a", "flac"], ".wav": ["-c:a", "pcm_s16le"]}[ext]
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(wav), *codec, str(out)], check=True)


def vocal_analysis(x, sr=SR, threads=16):
    """Separate with Demucs htdemucs on the CPU; report how much energy is in the vocal stem."""
    import numpy as np
    # htdemucs weights live inside the env (see docs/logs/yue2-setup.md for why).
    os.environ.setdefault("TORCH_HOME", str(Path(sys.prefix) / "share" / "torch-hub"))
    import torch
    from scipy.signal import resample_poly
    from demucs.apply import apply_model
    from demucs.pretrained import get_model
    torch.set_num_threads(threads)
    t0 = time.perf_counter()
    model = get_model("htdemucs")
    model.eval()
    y = resample_poly(x, model.samplerate, sr, axis=0).astype(np.float32)  # 48k -> 44.1k
    mix = torch.from_numpy(y.T.copy())
    ref = mix.mean(0)
    norm = (mix - ref.mean()) / (ref.std() + 1e-8)
    with torch.inference_mode():
        stems = apply_model(model, norm[None], device="cpu", shifts=1, split=True, overlap=0.25, progress=False)[0]
    stems = stems * (ref.std() + 1e-8) + ref.mean()
    names = list(model.sources)
    v = stems[names.index("vocals")].numpy().T
    acc = (stems.sum(0) - stems[names.index("vocals")]).numpy().T
    e = lambda a: float(np.mean(a.astype(np.float64) ** 2) + 1e-20)
    ratio_db = 10 * np.log10(e(v) / e(y))
    share = e(v) / (e(v) + e(acc))
    # Windowed: fraction of 1 s windows where the vocal stem is within 15 dB of the mix
    # (and the mix itself is not near-silent).
    w = model.samplerate
    k = len(y) // w
    vw = np.mean(v[: k * w].reshape(k, w, 2) ** 2, axis=(1, 2)) + 1e-20
    mw = np.mean(y[: k * w].reshape(k, w, 2) ** 2, axis=(1, 2)) + 1e-20
    active = (10 * np.log10(vw / mw) > -15) & (10 * np.log10(mw) > -50)
    worst = float(np.max(10 * np.log10(vw / mw))) if k else None
    return {"method": "demucs htdemucs (CPU), vocal stem vs mix",
            "vocal_to_mix_db": round(float(ratio_db), 2),
            "vocal_energy_share": round(float(share), 4),
            "vocal_active_windows_pct": round(100 * float(active.mean()), 1) if k else None,
            "loudest_1s_vocal_to_mix_db": round(worst, 2) if worst is not None else None,
            "separation_seconds": round(time.perf_counter() - t0, 1)}, v, acc, model.samplerate


def asr_check(vocal_stem, sr):
    """Second opinion when the vocal stem is busy: does Whisper hear words in it?

    Calibration (see docs/logs/yue2-setup.md): a sung YuE2 song's stem gives coherent
    lines with avg_logprob about -0.3..-0.8; a woodwind lead leaking into the stem
    gave no segments, or gibberish with no_speech_prob > 0.9.
    """
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from scipy.signal import resample_poly
    except ImportError:
        return {"skipped": "faster-whisper not installed"}
    model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=16,
                         download_root=str(Path(sys.prefix) / "share" / "whisper"))
    mono = resample_poly(vocal_stem.mean(axis=1), 16000, sr).astype(np.float32)
    segments, info = model.transcribe(mono, beam_size=5, condition_on_previous_text=False)
    segs = [{"start": round(x.start, 1), "end": round(x.end, 1), "no_speech": round(x.no_speech_prob, 2),
             "avg_logprob": round(x.avg_logprob, 2), "text": x.text.strip()[:120]} for x in segments]
    wordy = [x for x in segs if x["no_speech"] < 0.6 and x["avg_logprob"] > -1.0]
    return {"model": "faster-whisper small (CPU int8)", "language_guess": info.language,
            "segments": segs, "likely_words_seconds": round(sum(x["end"] - x["start"] for x in wordy), 1)}


# --------------------------------------------------------------------- hardware

def choose_hardware(args):
    """Pick weight dtype and low-VRAM mode from the visible GPU (overridable).

    YuE2 loads its weights as BF16. Turing cards (RTX 20xx, cc 7.5) report
    is_bf16_supported() == True, but BF16 matmuls are emulated there (measured on
    a 2080 Ti: 7 TFLOPS BF16 vs 44 TFLOPS FP16), so auto selects FP16 on them.
    The VAE stays FP32 in both cases (YuE2 enforces that).
    """
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("No CUDA GPU visible")
    dev = torch.device(args.device)
    props = torch.cuda.get_device_properties(dev)
    total = props.total_memory / 2**30
    native_bf16 = torch.cuda.is_bf16_supported(including_emulation=False)
    precision = args.precision if args.precision != "auto" else ("bf16" if native_bf16 else "fp16")
    low = {"on": True, "off": False}.get(args.low_vram, total < 20)
    attention_fix = None
    if props.major < 8 and torch.backends.cuda.is_flash_attention_available():
        # The torch wheel is built with FlashAttention, but FlashAttention needs sm80+.
        # YuE2 then keeps grouped-query attention (enable_gqa), which on Turing falls
        # back to the math kernel and materialises the full attention matrix (OOM in
        # NAR prefill). Reporting "unavailable" makes YuE2 take its documented
        # fallback for builds without FlashAttention: expand KV heads so the
        # memory-efficient SDPA kernel can run.
        torch.backends.cuda.is_flash_attention_available = lambda: False
        attention_fix = "expand_kv_for_mem_efficient_sdpa"
    if precision == "fp16":
        import yue2.modeling_yue2 as mm
        original = mm.YuE2ForCausalLM.from_pretrained  # bound classmethod

        def fp16_from_pretrained(*a, **k):
            k["torch_dtype"] = torch.float16  # pipeline hardcodes bfloat16 here
            return original(*a, **k)
        mm.YuE2ForCausalLM.from_pretrained = fp16_from_pretrained
    return {"gpu": props.name, "compute_capability": f"{props.major}.{props.minor}", "total_gib": round(total, 2),
            "native_bf16": native_bf16, "precision": precision, "low_vram": low,
            "attention_fix": attention_fix}


# --------------------------------------------------------------------- main

def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", required=True, help="style description: mood, instruments, tempo, structure")
    ap.add_argument("--seconds", type=float, default=75.0, help="target loop length in seconds")
    ap.add_argument("--out", type=Path, required=True, help=".ogg (default web format), .mp3, .wav or .flac")
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--lyrics", type=Path, help="song mode: sung lyrics; instrumental mode: planner section tags only")
    ap.add_argument("--instrumental", action="store_true", help="official score-transfer instrumental recipe")
    ap.add_argument("--cot", choices=("full", "melody", "off"), default="full", help="song mode planning (default full)")
    ap.add_argument("--no-trim-score", action="store_true", help="instrumental: render the whole planned score")
    ap.add_argument("--abc", type=Path, help="instrumental: render this YuE2 ABC score instead of planning a new one "
                    "(e.g. a saved score-instrumental.abc; Vocal notes are moved to Ins first)")
    ap.add_argument("--max-overrun", type=float, default=1.3,
                    help="instrumental: cap semantic tokens at this multiple of the score's nominal length "
                         "(0 = model default 9000). On Turing GPUs the song stream can run far past the score.")
    ap.add_argument("--skip-hash-check", action="store_true",
                    help="skip YuE2's sha256 check of the 7.3 GB weights at load (saves 17-77 s)")
    ap.add_argument("--lufs", type=float, default=-16.0)
    ap.add_argument("--xfade", type=float, default=2.0, help="loop crossfade seconds")
    ap.add_argument("--no-separation", action="store_true", help="skip the Demucs vocal check")
    ap.add_argument("--keep-stems", action="store_true", help="also write the accompaniment stem (fallback)")
    ap.add_argument("--work", type=Path, help="native artifacts dir (default $FORGE_RUNTIME/work/bgm/<id>)")
    ap.add_argument("--model", default="m-a-p/YuE2-3B")
    ap.add_argument("--vae", default="m-a-p/YuE2-Vae")
    ap.add_argument("--revision")
    ap.add_argument("--vae-revision")
    ap.add_argument("--cache-dir", help="HF hub cache; default follows HF_HUB_CACHE / HF_HOME")
    ap.add_argument("--offline", action="store_true", help="never contact the Hub")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--precision", choices=("auto", "bf16", "fp16"), default="auto",
                    help="model weights dtype; auto = bf16 on GPUs with native BF16 (Ampere+), else fp16")
    ap.add_argument("--low-vram", choices=("auto", "on", "off"), default="auto",
                    help="official low_vram mode (AR/NAR experts swapped via CPU); auto = on below 20 GiB")
    ap.add_argument("--memory-budget", type=float, help="YuE2 memory_budget_gib (default 24; 2 GiB is reserved)")
    args = ap.parse_args(argv)

    t_start = time.perf_counter()
    report = {"ok": False, "out": str(args.out), "mode": "instrumental" if args.instrumental else "song",
              "seed": args.seed, "requested_seconds": args.seconds}
    try:
        return run(args, report, t_start)
    except Exception as exc:  # one JSON line on failure too
        report.update(error=f"{type(exc).__name__}: {exc}")
        print(json.dumps(report, ensure_ascii=False))
        import traceback
        traceback.print_exc()
        return 2


def run(args, report, t_start) -> int:
    import numpy as np
    import soundfile as sf

    sys.path.insert(0, str(skill_scripts()))
    from instrumental import lyric_tags, validate_score  # official helper module
    from instrumentalize import convert_score

    out = args.out.resolve()
    if out.suffix.lower() not in (".ogg", ".mp3", ".wav", ".flac"):
        raise ValueError("--out must end in .ogg, .mp3, .wav or .flac")
    out.parent.mkdir(parents=True, exist_ok=True)
    ident = re.sub(r"[^A-Za-z0-9_.-]", "-", out.stem)[:80] or "bgm"
    work = (args.work or runtime_root() / "work" / "bgm" / ident).resolve()
    if work.exists() and any(work.iterdir()):
        work = work.with_name(work.name + time.strftime("-%Y%m%dT%H%M%S"))
    work.mkdir(parents=True, exist_ok=True)

    user_lyrics = args.lyrics.read_text(encoding="utf-8") if args.lyrics else None
    if args.instrumental:
        style = instrumental_style(args.prompt)
        planning_lyrics = default_tags(args.seconds)
        if user_lyrics is not None:  # only keep section tags, never words
            tags = re.findall(r"\[[^\]\n]+\]", user_lyrics)
            planning_lyrics = "\n\n".join(tags) + "\n" if tags else default_tags(args.seconds)
    else:
        style = args.prompt.strip()
    report["style"] = style

    # ---- model -------------------------------------------------------------
    import torch
    from yue2 import YuE2Pipeline
    from yue2.protocol import SongRequest
    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    hw = choose_hardware(args)
    report["hardware"] = hw
    log(f"{hw['gpu']}: precision={hw['precision']} low_vram={hw['low_vram']}")
    kw = dict(vae=args.vae, revision=args.revision, vae_revision=args.vae_revision,
              cache_dir=args.cache_dir, device=args.device, low_vram=hw["low_vram"],
              verify_hashes=not args.skip_hash_check)
    if args.memory_budget:
        kw["memory_budget_gib"] = args.memory_budget
    t0 = time.perf_counter()
    try:
        pipe = YuE2Pipeline.from_pretrained(args.model, local_files_only=True, **kw)
    except Exception as exc:
        if args.offline:
            raise
        log(f"local model files not usable ({type(exc).__name__}); trying the Hub")
        pipe = YuE2Pipeline.from_pretrained(args.model, local_files_only=False, **kw)
    timings = {"resolve_and_verify_s": round(time.perf_counter() - t0, 1)}
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    status = "complete"
    with pipe:
        if args.instrumental:
            if args.abc:
                source_abc = args.abc.read_text(encoding="utf-8")
                report["score_source"] = str(args.abc)
            else:
                plan_req = SongRequest(id=ident, style=style, lyrics=planning_lyrics, cot="full", seed=args.seed)
                write_json(work / "planning-request.json", plan_req.to_dict())
                t0 = time.perf_counter()
                plan = pipe.plan(request=plan_req)
                timings["plan_s"] = round(time.perf_counter() - t0, 1)
                plan.save(work / "planning")
                if plan.truncated or not plan.abc:
                    raise RuntimeError("YuE2 score plan is empty or truncated")
                source_abc = plan.abc
            from abc_tools import parse_abc
            if parse_abc(source_abc).voices["Vocal"].notes:
                converted, transfer = convert_score(source_abc)
            else:
                converted, transfer = source_abc, {"nominal_seconds": None, "vocal_notes_before": 0}
            write_json(work / "transfer.json", transfer)
            trim = {"trimmed": False}
            if not args.no_trim_score:
                converted, trim = trim_score(converted, args.seconds)
            validate_score(converted)
            (work / "score-instrumental.abc").write_text(converted, encoding="utf-8")
            has_chords = bool(parse_abc(converted).voices["Vocal"].chords)
            request = dict(id=ident, style=style, lyrics=lyric_tags(converted), abc=converted,
                           cot="full" if has_chords else "melody", seed=args.seed)
            report["score"] = {"planned_nominal_seconds": transfer.get("nominal_seconds"),
                               "vocal_notes_moved": transfer.get("vocal_notes_before"), **trim}
        else:
            request = dict(id=ident, style=style, lyrics=user_lyrics or "", cot=args.cot, seed=args.seed)
        write_json(work / "render-request.json", request)
        extra = {}
        if args.max_overrun and request.get("abc"):
            from abc_tools import parse_abc, report as abc_report
            nominal = abc_report(parse_abc(request["abc"]))["nominal_duration_seconds"]
            cap = int(nominal * SEMANTIC_TOKENS_PER_SECOND * args.max_overrun) + 250
            if cap < 9000:
                extra["semantic_sampling"] = {"max_tokens": cap}
                report["semantic_token_cap"] = cap
        song = pipe(**request, **extra)
        song.save_artifacts(work / "native")
        for k in ("abc", "semantic"):
            t = song.timing.get(k) or {}
            timings[f"{k}_s"] = round(t.get("seconds", 0.0), 1)
            if t.get("output_tokens"):
                timings[f"{k}_tokens"] = t["output_tokens"]
        timings["nar_s"] = round(song.timing["nar_seconds"], 1)
        timings["vae_s"] = round(song.timing["vae_seconds"], 1)
        timings["render_e2e_s"] = round(song.timing["e2e_seconds"], 1)
        timings["load"] = {k: round(v, 1) for k, v in song.timing.get("load", {}).items()}
        if song.truncated.get("abc") or (song.truncated.get("semantic") and "semantic_token_cap" not in report):
            status = "needs_review"
        elif song.truncated.get("semantic"):
            status = "capped"  # stopped by --max-overrun on purpose; the loop is cut from what exists
        audio = np.asarray(song.audio, dtype=np.float32)
        model_rev = {"mot": pipe.model_dir.name, "vae": pipe.vae_dir.name}
    if torch.cuda.is_available():
        report["torch_peak_reserved_gib"] = round(torch.cuda.max_memory_reserved() / 2**30, 2)
        torch.cuda.empty_cache()
    timings["gpu_total_s"] = round(time.perf_counter() - t_start, 1)
    report["model_snapshot"] = model_rev
    report["truncated"] = song.truncated
    report["status"] = status

    # ---- measure + export (CPU) -------------------------------------------
    sf.write(work / "raw.wav", audio, SR, subtype="FLOAT")
    head, tail = silence_edges(audio)
    raw = {"seconds": round(len(audio) / SR, 2), "sample_rate": SR, "channels": int(audio.shape[1]),
           "lufs": round(loudness(audio), 2), "true_peak_dbtp": round(true_peak_db(audio), 2),
           "head_silence_s": round(head, 2), "tail_silence_s": round(tail, 2)}
    report["raw"] = raw

    if not args.no_separation:
        t0 = time.perf_counter()
        voc, v_stem, acc_stem, ssr = vocal_analysis(audio)
        if voc["vocal_active_windows_pct"] and voc["vocal_active_windows_pct"] >= 5:
            voc["asr"] = asr_check(v_stem, ssr)
        report["vocal_check"] = voc
        if args.keep_stems:
            sf.write(work / "stem-vocals.wav", v_stem, ssr, subtype="FLOAT")
            sf.write(work / "stem-accompaniment.wav", acc_stem, ssr, subtype="FLOAT")
        timings["separation_s"] = round(time.perf_counter() - t0, 1)

    bpm, beats = 90, 4
    abc_text = request.get("abc")
    if not abc_text and (work / "native" / "score.abc").is_file():
        abc_text = (work / "native" / "score.abc").read_text(encoding="utf-8")
    if abc_text:
        q = re.search(r"^Q:\s*1/4=(\d+)", abc_text, re.M)
        m = re.search(r"^M:\s*(\d+)/(\d+)", abc_text, re.M)
        if q:
            bpm = int(q.group(1))
        if m:
            beats = int(m.group(1)) * 4 / int(m.group(2))
    loop = choose_loop(audio, args.seconds, bpm, beats, args.xfade)
    body = crossfade_loop(audio, loop["start"], loop["length"], args.xfade)
    pre = loudness(body)
    gain_db = args.lufs - pre
    body = limit(body * 10 ** (gain_db / 20), -1.0)
    final_lufs = loudness(body)
    loop_wav = work / "loop.wav"
    sf.write(loop_wav, body, SR, subtype="PCM_24")
    encode(loop_wav, out)
    siblings = [out]
    if out.suffix.lower() != ".mp3":
        mp3 = out.with_suffix(".mp3")
        encode(loop_wav, mp3)
        siblings.append(mp3)
    report["loop"] = {"score_bpm": bpm, "beats_per_bar": beats, "start_s": round(loop["start"], 3),
                      "seconds": round(loop["length"], 3), "bars": loop["bars"],
                      "seam_similarity": loop["seam_similarity"], "crossfade_s": args.xfade,
                      "fallback": loop["fallback"], "lufs_before": round(pre, 2), "gain_db": round(gain_db, 2),
                      "lufs": round(final_lufs, 2), "true_peak_dbtp": round(true_peak_db(body), 2)}
    report["files"] = {"audio": [str(p) for p in siblings], "work": str(work)}
    sidecar = {"prompt": args.prompt, "style_sent": style, "seed": args.seed, "mode": report["mode"],
               "render_request": request}
    if (work / "planning-request.json").is_file():
        sidecar["planning_request"] = json.loads((work / "planning-request.json").read_text(encoding="utf-8"))
    write_json(out.with_suffix(".request.json"), sidecar)
    timings["total_s"] = round(time.perf_counter() - t_start, 1)
    report["timings"] = timings
    report["ok"] = True
    write_json(work / "report.json", report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if status in ("complete", "capped") else 1


if __name__ == "__main__":
    sys.exit(main())
