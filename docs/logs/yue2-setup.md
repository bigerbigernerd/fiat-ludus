# YuE2-3B as the background-music stage: setup and debugging log

Date: 2026-11-12. Machine: Ubuntu 24.04, 52 cores, 251 GB RAM, NVIDIA driver 595.99.02,
GPU 0 = RTX 3090 24 GB (reference), GPU 1 = RTX 2080 Ti 11 GB (target: the pipeline must
run on 11-12 GB cards). No sudo, no system CUDA toolkit, no system ffmpeg.

**Nobody has listened to any of the audio produced here.** Every statement about
vocals, loops and quality below comes from objective measurements (source separation,
speech recognition, loudness, level envelopes). Treat "no vocals detected" as "no vocals
detected by these tools", not as "verified instrumental".

Paths: `$FORGE_ROOT` is the project, `$FORGE_RUNTIME` the runtime root (repos, envs),
`$HF_HOME` / `$HUGGINGFACE_HUB_CACHE` the Hugging Face cache.

## TL;DR

- YuE2 is a lyrics-to-song model. Asking it directly for an instrumental does **not**
  work: with "Instrumental ... no vocals, no singing" in the style and only section tags
  as lyrics, it sang (vocal stem -4.0 dB vs mix, 36 % of 1-second windows active,
  speech recognition heard pseudo-English syllables). With empty lyrics it sang real-sounding
  English lines for 161 s.
- The **official instrumental recipe** (yue2-music skill, `instrumental/`) works: YuE2
  plans a score, every note of the `Vocal` part is moved to the `Ins` part, and the revised
  score is rendered with no sung words. Field-theme renders measured -40 to -58 dB vocal
  stem vs mix with 0 % active windows.
- It runs on the **RTX 2080 Ti 11 GB** in FP16 with YuE2's `low_vram` mode plus one
  attention work-around: peak 5.1-6.5 GB on the card. It is about 4x slower than the 3090
  (render 112 s vs 30 s for the same 66 s score).
- BF16 on Turing is emulated (7 TFLOPS vs 44 TFLOPS FP16 measured) and, without the
  work-around, ran out of memory in the acoustic (NAR) stage.
- Output is 48 kHz stereo. Length is controlled through the score (whole sections are
  dropped), not by a duration parameter; actual audio length tracked the score within a few
  seconds on 6 of 8 instrumental renders, but twice on the 2080 Ti the song stream ran
  far past the score (5670 and 2707 tokens). `make_bgm.py` now caps it.

## 1. Sources and versions

| Item | Value |
|---|---|
| Code | https://github.com/multimodal-art-projection/YuE `main`, commit `1116bea76032bee5668e5f097294d082fa0b84f0` (2026-11-01). Package `yue2-infer` 0.1.6 installed from this checkout |
| Instrumental recipe | `skills/yue2-music/instrumental/` in the same commit, skill version 1.2.0 (validation.json dated 2026-09-25). Its own setup pins the wheel `yue2-infer` 0.1.5; we use only its CPU helper modules (`abc_tools`, `instrumentalize`, `compile_score`, `instrumental.lyric_tags/validate_score`) on top of 0.1.6 |
| Main model | `m-a-p/YuE2-3B`, snapshot `8f24312f187763b9854adeec87e437e2b03bbff1`; `model.safetensors` 7,261,441,640 bytes, sha256 `1d55c42c...e9` (identical to the weights pinned by the skill at revision `14fc6c6f...`) |
| Decoder | `m-a-p/YuE2-Vae`, snapshot `152733a19ad43aa67e367f9b5503ef8075bb5126` (FP32) |
| Python env | conda (miniforge 26.5.3) prefix env, Python 3.12.16, ffmpeg 9.0.2 from conda-forge (libmp3lame, libvorbis, libopus) |
| Python packages | torch 2.10.0+cu128, torchaudio 2.10.0+cu128, transformers 4.57.6, huggingface-hub 0.36.2, numpy 2.2.6, soundfile 0.13.1, scipy 1.18.1, pyloudnorm 0.2.0, demucs 4.0.1, faster-whisper 1.2.1 (ctranslate2 4.9.0); installed with uv 0.11.28 |
| Separation model | Demucs `htdemucs` (`955717e8-8726e21a.th`, sha256 `8726e21a993978c7...faa8b4`) |
| ASR model | faster-whisper `small` (Systran/faster-whisper-small), CPU int8 |

Disk: env 8.7 GB (torch 1.7 GB, NVIDIA CUDA wheels 4.3 GB, triton 0.6 GB, Whisper small
464 MB, htdemucs 81 MB), YuE2-3B snapshot 7.30 GB, YuE2-Vae 0.53 GB, code checkout 7 MB
(+33 MB git history).

## 2. Licences (quoted)

**Code, agent skill, docs** (GitHub README): "Code, agent skill, and documentation:
Apache 2.0." The bundled instrumental helpers say: "The bundled instrumental helpers retain
their MIT license."

**Weights.** The Hugging Face model card metadata says `license: cc-by-nc-4.0`, and the
`LICENSE` file in the HF repo (snapshot `8f24312`) says: "The YuE2-3B, YuE2-Vae and
YuE2-Vae-legacy checkpoint weights are licensed under Creative Commons
Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)". That HF file contains no
statement about generated audio.

The GitHub repository's `MODEL_LICENSE` (commits `6c639ab` and `cbb3b06`, 2026-09-16)
is the same CC BY-NC 4.0 text **plus** an "Individual creator permission and academic use
(2026-09-16)" preamble that the HF copy does not have. Relevant sentences, verbatim:

> "the YuE2 model licensors grant personal users, content creators and musicians acting in
> an individual capacity permission to use the model weights free of charge to generate
> outputs, and to publish, distribute, sell, license or otherwise monetize those outputs,
> subject to the responsible-use conditions below."

> "The individual creator permission does not authorize commercial use of the model
> weights by companies. Companies wishing to use the model weights commercially should
> contact ryuanab@connect.ust.hk to discuss a commercial license"

> "Generated outputs are not subject to the model weights' NonCommercial restriction
> solely because they were generated with YuE2."

> "Credit on generated outputs is optional and is not a condition of this additional
> permission or of output monetization."

The GitHub README summarises: personal users/creators may "use YuE2 and monetize generated
outputs, with no fees or royalties"; academic use is free for non-commercial use;
"Commercial use by companies: Contact us". Note the mismatch: someone who reads only the
Hugging Face repo sees plain CC BY-NC 4.0. Not legal advice; whoever ships a game with this
music should read both texts.

## 3. Chronological log

### 3.1 Reading first (no GPU)

- The owner's earlier deployment (A5000, Sept 2026) used the CLI `yue2 generate --request
  req.json`, with requests `{id, style, lyrics, cot: "full", seed}`; ~2 min and ~10 GB per
  3-minute song.
- The GitHub README (news, 2026-09-25) announced "Instrumental generation and covers"
  through the yue2-music agent skill: "YuE2 writes the score by default; the skill moves
  the vocal melody into the instrumental part before rendering."
- `docs/generation.md`: "For instrumental music, use empty lyrics and the instrumental
  workflow ... Vocal rests in the score do not guarantee voice-free audio; listen to each
  result before delivering it as instrumental." The skill README: "纯音乐条件也不保证绝无人声"
  (the instrumental condition does not guarantee the absence of vocals).
- So there is **no instrumental flag** in the model or the pipeline API. The supported way
  is a score-level transformation: plan with section tags only, move `Vocal` notes to `Ins`,
  render with `lyrics` empty or only the section tags (the skill's validator rejects any
  sung words), and the style gets "Instrumental, ..., no vocals, no singing, no choir, no
  spoken words".
- Install scripts skimmed before running: `pyproject.toml` (pinned deps only),
  `skills/.../setup_runtime.py` (creates a venv and pip-installs a pinned wheel from the
  HF repo by sha256; we did not run it).

### 3.2 Downloads (shared with other agents)

- The brief said weights land in `$FORGE_RUNTIME/hf-cache`. They did not: this machine
  exports its own `HF_HOME` (a shared cache on a spinning disk) and the legacy
  `HUGGINGFACE_HUB_CACHE` globally, and the legacy variable wins over
  the `HF_HOME` set by `env.sh` (which also keeps an already-set `HF_HOME`). The weights are
  on a spinning disk. `make_bgm.py` therefore relies on normal huggingface_hub resolution
  and has `--cache-dir` only as an override.
- The first download (default xet transfer, four clients at once on a shared ~10 MB/s
  link) stalled: after 7 minutes `model.safetensors.incomplete` was stuck at 67 MB, then
  201 MB, and the xet log was full of "Decreased concurrency from 1 to 1; reason: success
  ratio below threshold". The coordinator restarted all downloads over plain HTTPS
  (`HF_HUB_DISABLE_XET=1`), which finished: YuE2-3B in 3930 s, YuE2-Vae 143 s later. A
  201 MB `.incomplete` blob from the stalled attempt is left in the cache.

### 3.3 Environment

```bash
~/miniforge3/bin/conda create -y -p $FORGE_RUNTIME/envs/yue2 -c conda-forge --override-channels python=3.12 ffmpeg
uv pip install --python $FORGE_RUNTIME/envs/yue2/bin/python torch==2.10.0 torchaudio==2.10.0 \
    --index-url https://download.pytorch.org/whl/cu128
cd $FORGE_RUNTIME/repos/YuE2 && uv pip install --python $FORGE_RUNTIME/envs/yue2/bin/python . demucs==4.0.1 pyloudnorm scipy
uv pip install --python $FORGE_RUNTIME/envs/yue2/bin/python faster-whisper
```

Errors on the way:

1. First `conda create` failed after 5 minutes:
   `[Errno 2] No such file or directory: '<conda pkgs cache>/libpython-3.12.16-ha57cdbe_0_cpython.conda'`.
   Cause: the conda package cache is shared, and another agent's conda run was
   extracting/cleaning the same package at the same time. Fix: rerun; it succeeded.
2. conda-forge `ffmpeg` drags in OpenVINO libraries (several hundred MB). Accepted;
   the alternative (a static ffmpeg) would have been another download.
3. Demucs could not fetch its weights:
   `urllib.error.URLError: <urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired>`.
   Cause: the certificate of `dl.fbaipublicfiles.com` expired on 2026-11-11 23:59:59 GMT,
   the day before this run (`openssl s_client` shows `notAfter=Nov 11 23:59:59 2026 GMT`).
   Fix: downloaded `955717e8-8726e21a.th` twice, once from the Meta URL with certificate
   checking off and once from an unrelated Hugging Face mirror (`anchor-flux/demucs-htdemucs`,
   `htdemucs.th`); both have sha256 `8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4`,
   which also matches the hash prefix Demucs puts in the file name. Stored under
   `$FORGE_RUNTIME/envs/yue2/share/torch-hub/hub/checkpoints/`; `make_bgm.py` sets
   `TORCH_HOME` to that folder.
4. The instrumental skill's own CLI (`instrumental.py run`) refuses to run unless
   `yue2-infer` is exactly 0.1.5, and loads the model twice (plan, then render). We install
   0.1.6 from the repo and call the same helper functions from `make_bgm.py` with a single
   model load.

The skill's CPU tests pass on this checkout: `python3 -B scripts/verify_bundle.py` →
`{"passed": true, "files": 26}`, and `scripts/test_tools.py` completes.

### 3.4 The GPU question: what does a 2080 Ti need?

Probe (`tools/gpu_run.sh bgm probe-<gpu> --gpu N -- python probe_gpu.py`, 4096² matmul):

| | RTX 3090 (cc 8.6) | RTX 2080 Ti (cc 7.5) |
|---|---|---|
| `torch.cuda.is_bf16_supported()` | True | **True** (emulation) |
| `is_bf16_supported(including_emulation=False)` | True | **False** |
| FP16 matmul | 62.1 TFLOPS | 43.9 TFLOPS |
| BF16 matmul | 62.1 TFLOPS | **7.0 TFLOPS** |
| total memory seen by torch | 23.56 GiB | 10.57 GiB |

YuE2's pipeline only checks `is_bf16_supported()`, so it happily starts on Turing.
Relevant code facts (yue2 0.1.6):

- The model is always loaded with `torch_dtype=torch.bfloat16`; the rest of the code takes
  its dtype from the weights, so FP16 needs only a different load dtype. `make_bgm.py` does
  this by wrapping `YuE2ForCausalLM.from_pretrained` (no repo edits).
- The only quantized path is experimental FP8 for the AR layers, which requires compute
  capability ≥ 8.9; neither card can use it. No 8-bit/4-bit path exists.
- `low_vram=True` (official, `docs/hardware.md`) keeps only the active AR or NAR experts on
  the GPU, swapping the other half through CPU RAM, and unloads the song model before the
  FP32 VAE decode.
- The allocator is capped at `min(memory_budget_gib - 2, total - 2)` GiB: **8.57 GiB on the
  2080 Ti**, whatever `--memory-budget` says.

Run 1 - 3090, BF16, default settings (reference). Command:

```bash
tools/gpu_run.sh bgm field1-3090-bf16 --gpu 0 -- $FORGE_RUNTIME/envs/yue2/bin/python pipeline/make_bgm.py \
  --instrumental --prompt "$P1" --seconds 75 --seed 1101 --keep-stems --work ... --out assets/_smoke/bgm/ref-3090-field1.ogg
```

Plan 13.5 s; render of a 67.8 s score: 1659 semantic tokens in 14.5 s (~114 tok/s), NAR
8.2 s, VAE 7.1 s → 66.3 s of 48 kHz stereo audio in 29.7 s. Card peak 8,821 MiB (ledger),
torch reserved 8.04 GiB. Vocal stem -55.4 dB.

Run 2 - 2080 Ti, forced BF16, `low_vram` on. AR decoding ran at 18.6 tok/s (it is memory
bound, so emulated BF16 hardly matters there), then the acoustic stage failed:

```
File ".../yue2/nar.py", line 149, in _prefill
    h = self._attention(q[0], k[0], v[0], causal=True)
File ".../yue2/modeling_yue2.py", line 48, in sdpa
    return F.scaled_dot_product_attention(
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.56 GiB. GPU 0 has a total capacity
of 10.57 GiB of which 3.12 GiB is free. ... this process has 7.44 GiB memory in use. 8.57 GiB allowed;
Of the allocated memory 7.13 GiB is allocated by PyTorch
```

i.e. NAR prefill attention needed 7.13 + 2.56 = 9.69 GiB against an 8.57 GiB cap, about
1.1 GiB short. Cause: the cu128 torch wheel is built with FlashAttention, so
`torch.backends.cuda.is_flash_attention_available()` is True, but FlashAttention needs sm80+.
YuE2 then passes grouped-query attention (`enable_gqa=True`) to SDPA; on sm75 neither flash nor
memory-efficient attention can take that, so PyTorch uses the math kernel, which
materialises the whole attention matrix. YuE2 already has the right fallback for "builds
without FlashAttention" (expand KV heads so memory-efficient SDPA runs, issue #209); it just
doesn't trigger. Fix in `make_bgm.py`, only on cards with compute capability < 8:
`torch.backends.cuda.is_flash_attention_available = lambda: False`.
This run (ledger: 378.7 s, peak 7,629 MiB, 21.1 Wh, exit 2) also generated 5,670
semantic tokens for a 79 s score (see 3.6).

Run 3 - 2080 Ti, FP16, `low_vram`, attention fix (the configuration `make_bgm.py` now picks
automatically), cold page cache: works. Card peak 6,507 MiB, torch reserved 6.12 GiB, audio
finite, vocal stem -58.6 dB.

Controls on the 3090 with fixed scores (`--abc`, same seed):

| Score | Card | Precision / mode | Semantic tokens | Audio | Render | Card peak | Vocal stem |
|---|---|---|---|---|---|---|---|
| 2080 Ti's 67.8 s score | 3090 | FP16 | 1748 | 69.9 s | 35.7 s | 8,805 MiB | -53.7 dB |
| same | 3090 | BF16 | 1762 | 70.4 s | 38.4 s | 8,805 MiB | -52.2 dB |
| same | 3090 | FP16 + `low_vram` | 1748 | 69.9 s | 41.3 s | 5,915 MiB | (bit-identical to FP16) |
| 3090's 67.8 s score | 3090 | BF16 | 1659 | 66.3 s | 29.7 s | 8,821 MiB | -55.4 dB |
| same | 2080 Ti | FP16 + `low_vram` | 1642 | 65.6 s | 112.2 s | 5,885 MiB | -58.0 dB |
| same | 2080 Ti | BF16 (emulated) + `low_vram` + fix | 1624 | 64.9 s | 125.4 s | 5,737 MiB | -56.4 dB |

BF16 does run on Turing once the attention fix is in: the song (AR) stage even ran at
39 tok/s, against 19 tok/s for FP16 and 18.6 tok/s for BF16 without the fix (not explained; the
FP16 AR stage did not get faster with the fix either). The acoustic (NAR) stage, which is
compute bound, took 73.4 s vs 19.1 s in FP16 (the 7 vs 44 TFLOPS gap). Net render time for a
65 s piece: BF16 125.4 s, FP16 112.2 s. `make_bgm.py` therefore picks FP16 on cards without
native BF16; `--precision bf16` stays available as the dtype the model was trained in.

Conclusions: FP16 does not produce NaNs or garbage by these checks (finite audio, normal
length, no vocal leakage, loudness in the same range). `low_vram` is exact (same tokens and
same loudness to 0.01 LU) and saves ~2.9 GB. Peak VRAM for a 60-100 s piece on the 2080 Ti
is 5.1-6.5 GB, well under 11 GB. FP16 vs BF16 audio cannot be compared sample by sample
because sampling differs per kernel; by the objective checks they are equivalent. Whether
they *sound* equivalent is unverified.

### 3.5 Load time (weights on the HDD)

YuE2 re-hashes the 7.3 GB weights on every load (`verify_hashes=True` by default).

| | hash check ("Verifying model files") | model load |
|---|---|---|
| cold page cache (evicted with `posix_fadvise(DONTNEED)`, 2080 Ti run) | 76.6 s (~95 MB/s disk) | 3.3 s |
| warm page cache, 3090 | 17.0-19.0 s | 0.3-0.4 s |
| warm page cache, 2080 Ti (`low_vram` loads to CPU first) | 16.9-19.5 s | 2.1-2.5 s |

`make_bgm.py --skip-hash-check` passes `verify_hashes=False` and saves these 17-77 s per
call; it is off by default. Moving the weights to the NVMe would cut the cold case.

### 3.6 Length control, and the stream that would not stop

There is no duration parameter. Length follows the score: the planner writes sections for
the section tags it is given (but freely: for the tags intro/verse/chorus/interlude/verse/
chorus/outro it wrote between 4 and 13 sections and 56-388 s), and `make_bgm.py` keeps whole
sections from the start until the target length is reached, at the note-event level using
the skill's event compiler (no text surgery). Actual audio vs nominal score length:

| Render | Card | Score (nominal) | Audio |
|---|---|---|---|
| field1 ref | 3090 | 67.8 s | 66.3 s |
| trimmed-score replay | 3090 | 79.1 s | 81.2 s |
| replays (3 runs) | 3090 | 67.8 s | 69.9-70.4 s |
| field1 | 2080 Ti FP16 | 67.8 s | **108.2 s** (2707 tokens) |
| first BF16 run | 2080 Ti BF16 | 79.1 s | **~227 s** (5670 tokens, then OOM) |
| 3090's score | 2080 Ti FP16 | 67.8 s | 65.6 s |
| field2 / field3 / combat1 (capped) | 2080 Ti FP16 | 70.6 / 97.0 / 49.7 s | 68.8 / 96.1 / 49.1 s |

Replaying the overrunning score on the 3090 rendered it at normal length, and the 2080 Ti
rendered the 3090's score at normal length, so it is neither the trimming nor FP16 nor
`low_vram`; it happened in 2 of the first 3 (uncapped) Turing renders and 0 of 5 Ampere renders. Not root-caused.
Guard: `--max-overrun` (default 1.3) sets `semantic_sampling.max_tokens` to 1.3 × nominal
seconds × 25 tokens/s + 250 (25 tok/s measured: 1659 tokens → 66.32 s). If the cap is hit
the run is reported as `"status": "capped"` and still exits 0, since the loop is cut from what
exists. None of the 7 later, capped Turing renders hit the cap (largest ratio 1.2x: an early combat render, 34.8 s for a 29.1 s score).

First version of the trim rule kept sections only while they fit inside 1.1 × target. For a
388 s plan whose intro is 23 s and next section ends at 97 s, that kept only the intro (26 s
of audio for a 75 s request; same for combat: 29 s for 45 s). Changed to "keep sections until
the target is reached", rerun with the same seeds; the seeded plans reproduced byte-for-byte
on the same card.

Starts/ends: head silence 0-0.46 s on all renders. Most renders end with a 2-4 s decay
(last-second level -18 to -67 dB relative to the piece), i.e. the model writes an ending even
for a cut score; combat1 ends abruptly at full level.

### 3.7 Instrumental findings (numbers)

Vocal check: Demucs htdemucs on CPU (~8-40 s per piece), reporting vocal-stem energy
relative to the mix, share of vocal energy, and the share of 1-second windows where the
vocal stem is within 15 dB of the mix. When ≥ 5 % of windows are active, faster-whisper
small transcribes the vocal stem as a second opinion.

Calibration:

| Input | Vocal stem vs mix | Active windows | Whisper on vocal stem |
|---|---|---|---|
| Known sung song (owner's earlier YuE2 Mandarin ballad, first 90 s) | -2.8 dB | 80 % | - |
| Known sung song (owner's earlier YuE2 English folk, first 60 s) | -5.8 dB | 47 % | coherent lyrics, avg_logprob -0.3 to -0.8 |
| Synthetic no-voice control (vibrato flute-like lead + pad) | -58.5 dB | 0 % | - |

Results (all with the field-1 style text unless stated):

| Request | Card | Vocal stem vs mix | Active windows | Whisper |
|---|---|---|---|---|
| **style "Instrumental, ... no vocals", lyrics = section tags only, no score transfer** | 2080 Ti | **-4.0 dB** (41 % of energy) | **36 %** | "She hear and say", "Oh, they so fence all" (avg_logprob -0.84) |
| **same, lyrics empty** | 2080 Ti | **-5.1 dB** | **54 %** | "Rose walked through the horizon, Ancient parrots ..." (161 s piece) |
| official recipe, field1 | 3090 | -55.4 dB | 0 % | - |
| official recipe, field1 | 2080 Ti | -58.6 dB | 0 % | - |
| official recipe, 2080 Ti's 79 s score replayed | 3090 | -17.1 dB | 14.8 % | no segments at all |
| official recipe, field2 (piano) | 2080 Ti | -40.0 dB | 0 % | - |
| official recipe, field3 (clarinet) | 2080 Ti | -58.2 dB | 0 % | - |
| official recipe, combat1 | 2080 Ti | -11.4 dB | 24.5 % | no segments at all |
| official recipe, combat2 | 2080 Ti | -25.5 dB | 6.6 % (29-33 s) | only "You" / "Thank you." (typical Whisper hallucination on non-speech; 0 s likely words) |

The baseline runs show why the transfer step exists: even with no words, the planner writes
notes into the `Vocal` part (25 notes in the tags-only run) and the renderer sings them.

Three official-recipe renders have flagged passages (combat2 at 29-33 s, too): the replay (17-33 s and 66-69 s) and
combat1 (mostly its last 8 s, 39-46 s, where the "vocal" stem carries essentially the whole
mix, plus 11-12 s). In both, Whisper found no speech, and the flagged stem is strongly tonal
(spectral flatness ≤ 0.0001, peaks at 590-785 Hz). This is consistent with a flute or brass
lead that Demucs assigns to its vocal stem, and equally with a wordless "ah" pad; only
listening can tell. The delivered combat1 loop (0.45-34.7 s) excludes the 39-46 s ending and
measures -26.9 dB, 0 % active as a file of its own. Combat2's flagged 29-33 s lies *inside*
its loop, yet separating the loop file alone gives -60.4 dB: Demucs assigns that passage to
the vocal stem or not depending on the surrounding audio. Borderline passages are therefore
unstable under this metric, and a clear singing voice is not (the baselines that sang stayed
at -4 to -6 dB).

Fallback tested on the two baselines that sang: keep Demucs's accompaniment stem. It
re-separates as -52.8 / -59.7 dB vocal (but that check uses the same model that did the
removal, so it says little about audible residue). Loudness drops 2.1-2.6 LU, because the
lead melody (the vocal line) is removed with the voice. Typical separation artefacts
(smearing, holes where the voice was) are expected but unmeasured. Wordless vocalise/humming
was not tested as a fallback because the official score-transfer route already gave
voice-free measurements.

### 3.8 Web versions and loops

`make_bgm.py` picks the loop from the render: start at the first non-silent 20 ms frame; length a
whole number of score bars (tempo and meter from the ABC `Q:`/`M:` fields), between 0.6 and
1.15 × target, chosen to maximise similarity between the 2 s after the loop end and the 2 s
after the loop start (correlation of log energy in 24 log-spaced bands, 50 ms hops) minus a
length penalty. The audio that follows the loop end is equal-power crossfaded (2 s) into the
loop head, so playing the file on repeat continues the music across the seam. Then gain to
-16 LUFS (integrated, pyloudnorm), a simple look-ahead limiter at -1 dBTP (4x oversampled
peaks), and encoding: OGG Vorbis `-q:a 4` (~122 kbit/s) plus MP3 160 kbit/s. Score bars
are nominal; the audio tempo can drift from them, so a bar-aligned seam is plausible, not
verified.

## 4. Deliverables

`assets/_smoke/bgm/` (each `.ogg` has an `.mp3` and a `.request.json` with the prompt, the
exact style text sent, the seed and the exact rendered request including the ABC score):

| File | Card | Prompt gist | Loop | Seam sim. | Vocal stem (loop) | Raw render |
|---|---|---|---|---|---|---|
| `field1` | 2080 Ti FP16 | flute, light strings, harp, 88 BPM, D major | 76.2 s (27 bars) | 0.42 | -58.2 dB, 0 % | 108.2 s |
| `field2` | 2080 Ti FP16 | solo piano over soft strings, 82 BPM, G major | 64.9 s (23 bars) | 0.79 | -42.5 dB, 0 % | 68.8 s |
| `field3` | 2080 Ti FP16 | clarinet, fingerpicked guitar, string pad, glockenspiel, 96 BPM, F major | 56.2 s (22 bars) | 0.85 | -57.9 dB, 0 % | 96.1 s |
| `combat1` | 2080 Ti FP16 | staccato strings, taiko/snare, brass + flute runs, 140 BPM, D minor | 34.3 s (20 bars) | 0.53 | -26.9 dB, 0 % (raw render flagged, see 3.7) | 49.1 s |
| `combat2` | 2080 Ti FP16 | same prompt, seed 4405 | 42.9 s (25 bars) | 0.64 | -60.4 dB, 0 % (raw render: 29-33 s flagged, see 3.7) | 61.4 s |
| `ref-3090-field1` | 3090 BF16 | field1 prompt and seed (different plan) | 56.5 s (20 bars) | 0.50 | -55.1 dB, 0 % | 66.3 s |

All loops measure -16.0 LUFS, true peak -1.0 to -2.3 dBTP. The planner set its own tempo
(85 BPM for both 82 and 88 BPM prompts, 94 for 96, 140 for 140); the prompt is guidance, not a
constraint. Native artifacts (raw FLAC, score, tokens, latents, stems) stay under
`$FORGE_RUNTIME/repos/YuE2/outputs/<run>/`, not in the project.

### Measured cost per piece (ledger, `cost/ledger.jsonl`, stage `bgm`)

| Job | Card | Wall time (incl. load, plan, Demucs, encode) | Peak card memory | Energy |
|---|---|---|---|---|
| field1 ref | 3090 | 90.5 s | 8,821 MiB | 3.7 Wh |
| field1 (cold cache, overran to 108 s) | 2080 Ti | 320.8 s | 6,507 MiB | 12.4 Wh |
| field2 | 2080 Ti | 142.6 s | 5,151 MiB | 5.7 Wh |
| field3 (rerun, 388 s plan, 99 s planning) | 2080 Ti | 264.8 s | 6,507 MiB | 12.5 Wh |
| combat1 (rerun) | 2080 Ti | 173.5 s (incl. 65 s Demucs + Whisper) | 5,151 MiB | 4.9 Wh |
| combat2 | 2080 Ti | 159.9 s (after waiting 898 s for the GPU lock) | 5,151 MiB | 6.6 Wh |
| same 66 s score | 3090 / 2080 Ti | 90.5 s / 165.6 s | 8,821 / 5,885 MiB | 3.7 / 6.6 Wh |

On the 2080 Ti the AR song stream runs at ~19 tokens/s (3090: ~114), i.e. about 1.3 s of GPU
time per second of music for that stage; planning takes 21-33 s (99 s for the 13-section
plan). The CPU checks (Demucs, Whisper) run inside the locked job; `--no-separation` skips
them.

## 5. `pipeline/make_bgm.py`

```
$FORGE_RUNTIME/envs/yue2/bin/python pipeline/make_bgm.py --prompt "<style>" --seconds 75 --out out.ogg \
    [--seed N] [--lyrics FILE] [--instrumental]
```

Without `--instrumental` the request goes to YuE2 unchanged (song mode). Useful extras:
`--abc FILE` (render a saved score again, e.g. a combat variant of a field score),
`--precision auto|bf16|fp16`, `--low-vram auto|on|off`, `--max-overrun`, `--skip-hash-check`,
`--no-separation`, `--keep-stems`, `--offline`, `--work DIR` (default
`$FORGE_RUNTIME/work/bgm/<name>`). Prints one JSON line (timings, raw and loop durations,
loudness, true peak, silence, vocal check, files); exit 0 ok / 1 needs review (truncated) / 2
failure.

## 6. Reproduce from scratch

```bash
cd $FORGE_ROOT && . ./env.sh
git clone https://github.com/multimodal-art-projection/YuE $FORGE_RUNTIME/repos/YuE2
git -C $FORGE_RUNTIME/repos/YuE2 checkout 1116bea76032bee5668e5f097294d082fa0b84f0
~/miniforge3/bin/conda create -y -p $FORGE_RUNTIME/envs/yue2 -c conda-forge --override-channels python=3.12 ffmpeg
PY=$FORGE_RUNTIME/envs/yue2/bin/python
uv pip install --python $PY torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
uv pip install --python $PY $FORGE_RUNTIME/repos/YuE2 demucs==4.0.1 pyloudnorm scipy faster-whisper
# weights (plain HTTPS was reliable here; xet stalled with several parallel clients)
HF_HUB_DISABLE_XET=1 hf download m-a-p/YuE2-3B --revision 8f24312f187763b9854adeec87e437e2b03bbff1
HF_HUB_DISABLE_XET=1 hf download m-a-p/YuE2-Vae --revision 152733a19ad43aa67e367f9b5503ef8075bb5126
# htdemucs weights (Meta's CDN certificate expired 2026-11-11; verify the sha256 whatever the source)
mkdir -p $FORGE_RUNTIME/envs/yue2/share/torch-hub/hub/checkpoints
curl -L -o $FORGE_RUNTIME/envs/yue2/share/torch-hub/hub/checkpoints/955717e8-8726e21a.th \
  https://huggingface.co/anchor-flux/demucs-htdemucs/resolve/main/htdemucs.th
sha256sum $FORGE_RUNTIME/envs/yue2/share/torch-hub/hub/checkpoints/955717e8-8726e21a.th
#   expect 8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4
# one piece on an 11 GB card (Whisper small downloads on first flagged run)
tools/gpu_run.sh bgm field --gpu 1 -- $PY pipeline/make_bgm.py --instrumental \
  --prompt "Bright pastoral open-field adventure theme, gentle and hopeful, solo flute melody over light sustained strings and soft harp arpeggios, sparse arrangement with lots of air, 88 BPM, D major" \
  --seconds 75 --seed 1101 --out assets/_smoke/bgm/field1.ogg
```

## 7. Open points

- Nobody has listened. Before shipping, listen for: voices or choir in the flagged
  passages, seam clicks/jumps at the loop point, and whether FP16 on the 2080 Ti sounds like
  BF16 on the 3090.
- The Turing-only song-stream overrun is not root-caused (2 of 6 renders).
- Licence wording differs between the Hugging Face `LICENSE` and the GitHub `MODEL_LICENSE`.
- Prompted tempo/key are not followed exactly (85 BPM for an 88 BPM request).
