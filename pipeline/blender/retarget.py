"""Humanoid motion retargeting for headless Blender 5.x.

Two pieces:

1. Semantic labelling of an armature: which bone is the hips, the spine chain,
   neck, head, and the left/right arm and leg chains. `label_by_names` knows the
   common naming schemes (Rigify/UAL "DEF-", Unreal, Mixamo, VRoid); `label_by_topology`
   works on anonymous skeletons such as UniRig's "bone_0..N" by walking the bone
   tree and looking at rest-pose positions (character Z-up, facing -Y, +X = its left).

2. `retarget_action`: transfers a clip from a source armature to a target
   armature in world space, corrected for different rest poses:

       for every mapped bone pair (s -> t):
         align  = shortest rotation turning the source bone's rest direction into
                  the target bone's rest direction (directions taken joint->next joint)
         ref_s  = align * rest_s                   # source bone "posed like the target rest"
         D(f)   = pose_s(f) * ref_s^-1             # world-space change at frame f
         pose_t(f) = D(f) * rest_t                 # same change applied to the target

   Hips translation is the source hips offset from rest scaled by the ratio of hip
   heights. Local (parent-relative) quaternions are then solved down the target
   hierarchy and written straight into F-curves of a new action. Unmapped target
   bones keep their rest orientation relative to their parent.

Everything is armature-space; both armature objects must have identity transforms.
"""
import math
import re

import bpy
from mathutils import Matrix, Quaternion, Vector

SIDES = ("L", "R")
LIMB_KEYS = {
    "arm": ("shoulder", "upper_arm", "forearm", "hand"),
    "leg": ("thigh", "shin", "foot", "toe"),
}


# --------------------------------------------------------------------------------------
# Semantic labelling
# --------------------------------------------------------------------------------------

NAME_PATTERNS = {
    # key: list of regexes tried in order (case-insensitive) against the bone name with the
    # side token removed. {S} expands to the side marker variants.
    "hips": [r"^(def-)?hips?$", r"^pelvis$", r"^mixamorig:hips$", r"^j_bip_c_hips$"],
    "neck": [r"^(def-)?neck(_?0?1)?$", r"^mixamorig:neck$", r"^j_bip_c_neck$"],
    "head": [r"^(def-)?head$", r"^mixamorig:head$", r"^j_bip_c_head$"],
    "shoulder": [r"^(def-)?shoulder$", r"^clavicle$", r"^mixamorig:shoulder$", r"^j_bip_shoulder$"],
    "upper_arm": [r"^(def-)?upper_?arm$", r"^mixamorig:arm$", r"^j_bip_upperarm$"],
    "forearm": [r"^(def-)?fore_?arm$", r"^lower_?arm$", r"^mixamorig:forearm$", r"^j_bip_lowerarm$"],
    "hand": [r"^(def-)?hand$", r"^mixamorig:hand$", r"^j_bip_hand$"],
    "thigh": [r"^(def-)?thigh$", r"^mixamorig:upleg$", r"^j_bip_upperleg$", r"^upper_?leg$"],
    "shin": [r"^(def-)?shin$", r"^calf$", r"^mixamorig:leg$", r"^j_bip_lowerleg$", r"^lower_?leg$"],
    "foot": [r"^(def-)?foot$", r"^mixamorig:foot$", r"^j_bip_foot$"],
    "toe": [r"^(def-)?toes?$", r"^ball$", r"^mixamorig:toebase$", r"^j_bip_toebase$"],
}
SPINE_RE = [r"^(def-)?spine[._]?0*\d*$", r"^mixamorig:spine\d*$", r"^j_bip_c_(spine|chest|upperchest)$"]


def _split_side(name):
    """Return (name without side token, 'L'/'R'/None)."""
    low = name.lower()
    m = re.search(r"[._\-](l|r|left|right)$", low)            # upper_arm.L, upperarm_l
    if m:
        return low[:m.start()], "L" if m.group(1).startswith("l") else "R"
    m = re.match(r"^(mixamorig:)(left|right)(.*)$", low)       # mixamorig:LeftArm
    if m:
        return m.group(1) + m.group(3), "L" if m.group(2) == "left" else "R"
    m = re.match(r"^(j_bip_)(l|r)_(.*)$", low)                 # J_Bip_L_UpperArm (VRoid)
    if m:
        return m.group(1) + m.group(3), "L" if m.group(2) == "l" else "R"
    m = re.match(r"^(left|right)[_ ]?(.*)$", low)
    if m:
        return m.group(2), "L" if m.group(1) == "left" else "R"
    return low, None


def label_by_names(arm):
    """Label a humanoid armature from bone names. Returns dict or None if incomplete."""
    lab = {"spine": []}
    for b in arm.data.bones:
        base, side = _split_side(b.name)
        if side is None and any(re.match(p, base) for p in SPINE_RE):
            lab["spine"].append(b.name)
            continue
        for key, pats in NAME_PATTERNS.items():
            if any(re.match(p, base) for p in pats):
                k = key if key in ("hips", "neck", "head") else f"{key}.{side}" if side else None
                if k and k not in lab:
                    lab[k] = b.name
                break
    # order spine bottom -> top by hierarchy depth
    lab["spine"].sort(key=lambda n: _depth(arm.data.bones[n]))
    needed = ["hips", "head"] + [f"{k}.{s}" for s in SIDES for k in ("upper_arm", "forearm", "hand", "thigh", "shin", "foot")]
    if all(k in lab for k in needed) and plausible(arm, lab):
        return lab
    return None


def plausible(arm, lab):
    """Do the named bones sit where a humanoid's would (Z up)? Names alone are not enough:
    UniRig's 'vroid' class emits VRoid names in sequence order even when the predicted
    joints are in a different order, so e.g. 'J_Bip_L_Hand' can sit on the shoulder."""
    B = arm.data.bones
    h = lambda k: B[lab[k]].head_local  # noqa: E731
    try:
        if not h("head").z > h("hips").z:
            return False
        for s in SIDES:
            if not (h(f"thigh.{s}").z > h(f"shin.{s}").z > h(f"foot.{s}").z):
                return False
            if not h(f"shin.{s}").z < h("hips").z:
                return False
            # hand farther from the body axis than the elbow, elbow farther than the shoulder joint
            r = lambda k: math.hypot(h(k).x, h(k).y)  # noqa: E731
            if not (r(f"hand.{s}") > r(f"forearm.{s}") > r(f"upper_arm.{s}") * 0.9):
                return False
            if lab[f"hand.{s}"] in (lab.get("head"), lab.get("hips")):
                return False
        # the two sides must be on opposite sides of the body
        if (h("hand.L").x > 0) == (h("hand.R").x > 0):
            return False
    except KeyError:
        return False
    return True


def _depth(b):
    d = 0
    while b.parent:
        b = b.parent
        d += 1
    return d


def _path_to_root(b):
    out = []
    while b:
        out.append(b)
        b = b.parent
    return out[::-1]


def label_by_topology(arm, height=None):
    """Label an anonymous humanoid skeleton (e.g. UniRig output) from its tree and rest pose.

    Assumes Z-up, character facing -Y, character's left = +X, feet near z=0.
    Raises ValueError with a reason when the skeleton does not look humanoid.
    """
    bones = list(arm.data.bones)
    if not bones:
        raise ValueError("armature has no bones")
    head_of = {b.name: b.head_local.copy() for b in bones}
    zs = [v.z for v in head_of.values()] + [b.tail_local.z for b in bones]
    z0 = min(zs)                       # heights below are measured from the lowest joint,
    H = height or (max(zs) - z0)       # so a skeleton centred on the origin works too
    leaves = [b for b in bones if not b.children]

    # Feet: lowest leaf on each side (tails count: a toe leaf's tail is lowest).
    def low(b):
        return min(b.head_local.z, b.tail_local.z) - z0
    left = [b for b in leaves if b.head_local.x > 0.02 * H]
    right = [b for b in leaves if b.head_local.x < -0.02 * H]
    if not left or not right:
        raise ValueError("no leaf bones on both sides of the body")
    foot_leaf = {"L": min(left, key=low), "R": min(right, key=low)}
    for s in SIDES:
        if low(foot_leaf[s]) > 0.25 * H:
            raise ValueError(f"lowest {s} leaf is at {low(foot_leaf[s]):.2f}, no leg reaches the ground")

    # Hips = lowest common ancestor of the two foot leaves.
    pl = _path_to_root(foot_leaf["L"])
    pr = _path_to_root(foot_leaf["R"])
    lca = None
    for a, b in zip(pl, pr):
        if a == b:
            lca = a
    if lca is None:
        raise ValueError("feet are in disconnected trees")
    hips = lca

    # Head: highest leaf (by tail) that is not a descendant of a leg chain.
    leg_set = set(pl) | set(pr)
    cand = [b for b in leaves if b not in leg_set]
    if not cand:
        raise ValueError("no upper-body leaves")
    head_leaf = max(cand, key=lambda b: max(b.head_local.z, b.tail_local.z))
    spine_path = _path_to_root(head_leaf)
    if hips not in spine_path:
        raise ValueError("head is not below the hips in the tree")
    spine_path = spine_path[spine_path.index(hips):]  # hips ... head_leaf

    # Hands: leaves (not legs, not on the spine path) farthest from the body axis on each side,
    # above knee height.
    spine_set = set(spine_path)
    hand_leaf = {}
    for s, sign in (("L", 1), ("R", -1)):
        c = [b for b in leaves if b not in leg_set and b not in spine_set
             and sign * b.tail_local.x > 0.05 * H and b.tail_local.z - z0 > 0.3 * H]
        if not c:
            raise ValueError(f"no arm found on side {s}")
        hand_leaf[s] = max(c, key=lambda b: sign * b.tail_local.x + 0.0 * b.tail_local.z)

    lab = {"hips": hips.name}
    # Arm chain: from the bone after the spine branch point to the hand leaf.
    branch = {}
    for s in SIDES:
        path = _path_to_root(hand_leaf[s])
        i = max(k for k, b in enumerate(path) if b in spine_set)
        branch[s] = path[i]
        chain = path[i + 1:]
        lab.update(_label_arm(chain, s, H))
    # Spine: bones strictly between hips and the neck. Neck = spine bone just above the
    # higher arm branch point; head = the next one (or the leaf).
    top_branch = max((branch[s] for s in SIDES), key=lambda b: spine_path.index(b))
    ib = spine_path.index(top_branch)
    upper = spine_path[ib + 1:]
    if len(upper) >= 2:
        lab["neck"], lab["head"] = upper[0].name, upper[1].name
    elif len(upper) == 1:
        lab["head"] = upper[0].name
    else:
        lab["head"] = top_branch.name
    lab["spine"] = [b.name for b in spine_path[1:ib + 1]]
    if top_branch == hips:
        lab["spine"] = []
    for s in SIDES:
        path = _path_to_root(foot_leaf[s])
        chain = path[path.index(hips) + 1:]
        lab.update(_label_leg(chain, s, H, z0))
    return lab


def _label_arm(chain, s, H):
    """chain: bones from first bone after the spine to the hand leaf (incl. finger bones)."""
    out = {}
    # Hand: the first bone whose head is past the point where the chain fans out into fingers,
    # else estimate from lengths: forearm ~ upper arm, hand ~ 0.1 H.
    names = [b.name for b in chain]
    fan = next((k for k, b in enumerate(chain) if len(b.children) > 1), None)
    if fan is not None:
        hand_i = fan
    else:
        # walk back from the tip until 0.09 H of chain length is covered
        acc, hand_i = 0.0, len(chain) - 1
        for k in range(len(chain) - 1, -1, -1):
            acc += chain[k].length
            hand_i = k
            if acc >= 0.09 * H:
                break
    pre = chain[:hand_i]
    out[f"hand.{s}"] = names[hand_i]
    if len(pre) >= 3:
        # shoulder (clavicle) is the bone that starts nearest the body centre
        out[f"shoulder.{s}"] = pre[0].name
        rest = pre[1:]
    else:
        rest = pre
    if len(rest) == 0:
        raise ValueError(f"arm {s}: no upper arm / forearm bones")
    if len(rest) == 1:
        out[f"upper_arm.{s}"] = rest[0].name
        out[f"forearm.{s}"] = rest[0].name
    else:
        # elbow = joint closest to halfway along the shoulder->wrist distance
        start, end = rest[0].head_local, chain[hand_i].head_local
        mid = (start + end) / 2
        j = min(range(1, len(rest)), key=lambda k: (rest[k].head_local - mid).length)
        out[f"upper_arm.{s}"] = rest[0].name
        out[f"forearm.{s}"] = rest[j].name
    return out


def _label_leg(chain, s, H, z0=0.0):
    out = {}
    if len(chain) < 2:
        raise ValueError(f"leg {s}: fewer than 2 bones")
    out[f"thigh.{s}"] = chain[0].name
    # ankle: first joint whose outgoing bone is more horizontal than vertical, or below 0.12 H
    ankle_i = None
    for k in range(1, len(chain)):
        b = chain[k]
        d = b.tail_local - b.head_local
        if b.head_local.z - z0 < 0.12 * H or abs(d.z) < math.hypot(d.x, d.y):
            ankle_i = k
            break
    if ankle_i is None:
        ankle_i = len(chain) - 1
    hip_z, ank = chain[0].head_local, chain[ankle_i].head_local
    mid = (hip_z + ank) / 2
    knees = range(1, ankle_i) if ankle_i > 1 else [1]
    kn = min(knees, key=lambda k: (chain[k].head_local - mid).length)
    out[f"shin.{s}"] = chain[kn].name
    out[f"foot.{s}"] = chain[ankle_i].name if ankle_i != kn else chain[-1].name
    if ankle_i + 1 < len(chain):
        out[f"toe.{s}"] = chain[ankle_i + 1].name
    return out


def facing_correction(arm, lab):
    """Degrees to turn about Z so the feet point along -Y; 0 if they already do."""
    B = arm.data.bones
    v = Vector((0, 0, 0))
    for side in SIDES:
        foot = lab.get(f"foot.{side}")
        toe = lab.get(f"toe.{side}")
        if foot is None:
            continue
        if toe and toe != foot:
            d = B[toe].tail_local - B[foot].head_local
        else:
            d = B[foot].tail_local - B[foot].head_local
        v += Vector((d.x, d.y, 0))
    if v.length < 1e-6:
        return 0
    ang = math.degrees(math.atan2(v.x, -v.y))      # 0 when v points to -Y, +90 at +X
    snapped = int(round(ang / 90.0)) * 90 % 360
    return 0 if snapped == 0 else -snapped          # turn back by that much


def pair_labels(src_lab, tgt_lab):
    """Return list of (target_bone, source_bone, key) pairs."""
    pairs = []
    for k, tv in tgt_lab.items():
        if k == "spine":
            continue
        sv = src_lab.get(k)
        if sv and tv:
            pairs.append((tv, sv, k))
    ts, ss = tgt_lab.get("spine", []), src_lab.get("spine", [])
    n, m = len(ts), len(ss)
    for i, t in enumerate(ts):
        if m == 0:
            break
        j = max(0, min(m - 1, round((i + 1) / n * m) - 1))
        pairs.append((t, ss[j], f"spine{i}"))
    # several target bones may map to one source bone (e.g. forearm == upper arm on a 1-bone arm)
    seen, out = set(), []
    for p in pairs:
        if p[0] not in seen:
            seen.add(p[0])
            out.append(p)
    return out


def chain_next(lab):
    """For each labelled key, the key of the joint its direction should point to."""
    nxt = {}
    sp = [f"spine{i}" for i in range(len(lab.get("spine", [])))]
    seq = ["hips"] + sp + (["neck"] if "neck" in lab else []) + ["head"]
    for a, b in zip(seq, seq[1:]):
        nxt[a] = b
    for s in SIDES:
        arm = [f"{k}.{s}" for k in LIMB_KEYS["arm"] if f"{k}.{s}" in lab]
        leg = [f"{k}.{s}" for k in LIMB_KEYS["leg"] if f"{k}.{s}" in lab]
        for ch in (arm, leg):
            for a, b in zip(ch, ch[1:]):
                nxt[a] = b
    return nxt


def keyed(lab):
    """label dict with spine list flattened to spine0..N keys."""
    d = {k: v for k, v in lab.items() if k != "spine"}
    for i, n in enumerate(lab.get("spine", [])):
        d[f"spine{i}"] = n
    return d


# --------------------------------------------------------------------------------------
# Retargeting
# --------------------------------------------------------------------------------------

def _rot(m):
    return m.to_3x3().normalized()


def rest_dirs(arm, lab_keyed, nxt):
    """Direction per key: from this joint's head to the next joint's head; chain ends
    reuse their parent key's direction (so the hand turns with the forearm)."""
    bones = arm.data.bones
    dirs = {}
    for k, name in lab_keyed.items():
        b = bones[name]
        if k in nxt and nxt[k] in lab_keyed:
            d = bones[lab_keyed[nxt[k]]].head_local - b.head_local
            if d.length > 1e-6:
                dirs[k] = d.normalized()
                continue
        dirs[k] = None
    # fill chain ends from the key pointing at them
    prev = {v: k for k, v in nxt.items()}
    for k in list(dirs):
        if dirs[k] is None:
            p = prev.get(k)
            while p is not None and dirs.get(p) is None:
                p = prev.get(p)
            dirs[k] = dirs.get(p) if p else (bones[lab_keyed[k]].tail_local - bones[lab_keyed[k]].head_local).normalized()
    return dirs


def build_alignment(src, tgt, src_lab, tgt_lab):
    """Per mapped key: (target bone, source bone, source reference rotation)."""
    sk, tk = keyed(src_lab), keyed(tgt_lab)
    # spine keys must be re-indexed through pair_labels' mapping
    pairs = pair_labels(src_lab, tgt_lab)
    nxt_t = chain_next(tgt_lab)
    tdirs = rest_dirs(tgt, tk, nxt_t)
    # source directions: computed along the *source* chain for the source bone each key maps to
    nxt_s = chain_next(src_lab)
    sdirs_by_bone = {}
    for k, d in rest_dirs(src, sk, nxt_s).items():
        sdirs_by_bone[sk[k]] = d
    out = []
    for tname, sname, key in pairs:
        kt = key
        td = tdirs.get(kt)
        sd = sdirs_by_bone.get(sname)
        if td is None or sd is None:
            align = Quaternion()
        else:
            align = sd.rotation_difference(td)
        if key == "hips":
            align = Quaternion()          # keep pelvis facing; only limbs/spine are re-aimed
        rest_s = _rot(src.data.bones[sname].matrix_local).to_quaternion()
        ref_s = align @ rest_s
        out.append((tname, sname, ref_s, key))
    return out


def retarget_action(src, tgt, src_lab, tgt_lab, src_action, frame_range, name,
                    root_motion=False, scale=None, frame_offset=0, dst_action=None):
    """Bake `src_action` (playing on `src`) onto `tgt`. Returns the new action.

    root_motion=False removes the hips' horizontal drift (keeps vertical bob).
    """
    scene = bpy.context.scene
    align = build_alignment(src, tgt, src_lab, tgt_lab)
    amap = {t: (s, ref) for t, s, ref, k in align}
    hips_t, hips_s = tgt_lab["hips"], src_lab["hips"]
    if scale is None:
        scale = tgt.data.bones[hips_t].head_local.z / max(1e-6, src.data.bones[hips_s].head_local.z)

    src.animation_data_create()
    src.animation_data.action = src_action
    if src_action.slots and src.animation_data.action_slot is None:
        src.animation_data.action_slot = src_action.slots[0]

    tbones = sorted(tgt.data.bones, key=_depth)
    rest_t = {b.name: b.matrix_local.copy() for b in tbones}
    rest_t_inv = {n: m.inverted() for n, m in rest_t.items()}
    src_rest_hips = src.data.bones[hips_s].head_local.copy()

    f0, f1 = frame_range
    frames = list(range(int(f0), int(f1) + 1))
    samples = {b.name: [] for b in tbones}     # list of (loc, quat)
    first_hips = None
    for f in frames:
        scene.frame_set(f)
        P = {}
        for b in tbones:
            n = b.name
            par = b.parent
            if par is None:
                C = rest_t[n]
            else:
                C = P[par.name] @ rest_t_inv[par.name] @ rest_t[n]
            if n in amap:
                sname, ref = amap[n]
                ps = src.pose.bones[sname].matrix
                q_s = _rot(ps).to_quaternion()
                D = q_s @ ref.inverted()
                q_t = D @ _rot(rest_t[n]).to_quaternion()
                if n == hips_t:
                    pos_s = ps.translation
                    off = (pos_s - src_rest_hips) * scale
                    if first_hips is None:
                        first_hips = off.copy()
                    if not root_motion:
                        off.x -= first_hips.x
                        off.y -= first_hips.y
                    pos = rest_t[n].translation + off
                    desired = Matrix.Translation(pos) @ q_t.to_matrix().to_4x4()
                    B = C.inverted() @ desired
                else:
                    Cq = _rot(C).to_quaternion()
                    B = (Cq.inverted() @ q_t).to_matrix().to_4x4()
            else:
                B = Matrix.Identity(4)
            P[n] = C @ B
            loc, q, _ = B.decompose()
            samples[n].append((loc, q))

    act = dst_action or bpy.data.actions.new(name)
    tgt.animation_data_create()
    tgt.animation_data.action = act
    for b in tbones:
        tgt.pose.bones[b.name].rotation_mode = "QUATERNION"
    for b in tbones:
        n = b.name
        s = samples[n]
        # quaternion sign continuity
        prev = None
        quats = []
        for _, q in s:
            if prev is not None and prev.dot(q) < 0:
                q = -q
            quats.append(q)
            prev = q
        moving_rot = any(abs(q.angle) > 1e-5 for q in quats)
        moving_loc = n == hips_t
        if not (moving_rot or moving_loc or n in amap):
            continue
        dp = f'pose.bones["{n}"]'
        for i in range(4):
            _write_curve(act, tgt, dp + ".rotation_quaternion", i, frames, [q[i] for q in quats], frame_offset, n)
        if moving_loc:
            for i in range(3):
                _write_curve(act, tgt, dp + ".location", i, frames, [l[i] for l, _ in s], frame_offset, n)
    return act


def _write_curve(act, obj, data_path, index, frames, values, offset, group):
    fc = act.fcurve_ensure_for_datablock(obj, data_path, index=index, group_name=group)
    start = len(fc.keyframe_points)
    fc.keyframe_points.add(len(frames))
    co = []
    for f, v in zip(frames, values):
        co += [f + offset, v]
    pts = fc.keyframe_points
    if start == 0:
        pts.foreach_set("co", co)
    else:
        for k, (f, v) in enumerate(zip(frames, values)):
            pts[start + k].co = (f + offset, v)
    for p in pts:
        p.interpolation = "LINEAR"
    fc.update()
