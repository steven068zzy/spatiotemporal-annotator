"""Reads and writes labels/<clip>.json. One file per clip, whole-file rewrites.

A whole-file rewrite through a temp file and `os.replace` rather than an append log: a
clip document is tens of kilobytes, the rewrite is atomic on every platform that matters,
and a half-written label file is the one failure that would cost a day of annotation.
"""

import json
import os
import time

from . import core as cc


def seed_clip(project, clip_id, meta, track_frames, zones, annotator=""):
    """A brand new document: one row per track, nothing confirmed yet."""
    n = meta["n_frames"]
    states = project.states.as_dict()

    def sort_key(tid):
        first = min(track_frames[tid]) if track_frames[tid] else 0
        box = track_frames[tid][first] if track_frames[tid] else [0, 0, 0, 0]
        return (zones.get(tid) or "", box[1], box[0])   # zone, then top, then left

    rows = []
    for i, tid in enumerate(sorted(track_frames, key=sort_key)):
        rows.append(cc.new_row("i%02d" % i, tid, zones.get(tid), "tracker"))

    doc = {
        "clip": clip_id,
        "video": meta.get("video"),
        "n_frames": n,
        "fps": meta["fps"],
        "w": meta["w"],
        "h": meta["h"],
        "zones": sorted({z for z in zones.values() if z}),
        "states": [dict(s) for s in project.states.states],
        "annotator": annotator or project.config.get("annotator", ""),
        "tool_version": _version(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "complete": False,
        "note": "",
        "individuals": cc.reconcile(rows, track_frames, n, meta["fps"], states=states),
    }
    return doc


def _version():
    try:
        from . import __version__
        return __version__
    except Exception:
        return "unknown"


def save(project, doc):
    os.makedirs(project.labels_dir, exist_ok=True)
    doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    final = project.label_path(doc["clip"])
    tmp = final + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f)
    os.replace(tmp, final)


def load(project, clip_id):
    p = project.label_path(clip_id)
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def load_all(project):
    """Every document on disk. A file we cannot parse is skipped rather than fatal: the
    progress panel must never take the annotator down."""
    d = project.labels_dir
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json") or fn.endswith("__SKIP.json"):
            continue
        try:
            with open(os.path.join(d, fn)) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        if "individuals" in doc:
            out.append(doc)
    return out


def skip(project, clip_id, annotator=""):
    os.makedirs(project.labels_dir, exist_ok=True)
    with open(project.skip_path(clip_id), "w") as f:
        json.dump({"clip": clip_id, "skipped": True, "annotator": annotator,
                   "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)


def unskip(project, clip_id):
    p = project.skip_path(clip_id)
    if os.path.exists(p):
        os.remove(p)


def require_zone(doc):
    """Whether THIS clip's rows need a zone to count as work.

    Per clip, not per project. A corpus can mix a two enclosure camera view with a single
    arena recording, and `ingest --zones-file` sets zones for some clips and not others,
    so a project level flag would be wrong for half the corpus. The clip document records
    the zones it was ingested with, and that is the authority.
    """
    return bool(doc.get("zones"))


def countable(doc, require_zone_=None):
    rz = require_zone(doc) if require_zone_ is None else require_zone_
    return [b for b in doc["individuals"] if cc.countable(b, rz)]


def clip_status(doc, require_zone_=None):
    live = countable(doc, require_zone_)
    done = sum(1 for b in live if b["status"] == "confirmed")
    return {"done": done, "total": len(live), "complete": bool(doc.get("complete"))}


def mark_complete(doc, require_zone_=None, census_mode=False):
    """Set the complete flag. In census mode every live row must be confirmed first.

    Outside census mode a clip can be marked complete whenever the annotator says so,
    which is what a user labelling two animals out of forty needs. The flag still means
    the same thing in both modes, namely "I am finished with this clip", and the export
    carries it so a downstream analysis can filter on it.
    """
    st = clip_status(doc, require_zone_)
    if census_mode and (st["done"] < st["total"] or st["total"] == 0):
        return False
    doc["complete"] = True
    return True


def tracks_of(doc):
    """{track_id: {frame: box}} rebuilt from the document's own rows.

    The tracks file is the seed, but after a re-anchor the document is the authority on
    which track holds which frame, so the invariant is enforced against the document and
    never against a re-read of the file.
    """
    tf = {}
    for b in doc["individuals"]:
        for f, box in enumerate(b.get("boxes") or []):
            if box is None:
                continue
            tid = cc.active_track(b["segments"], f)
            tf.setdefault(tid, {})[f] = box
    return tf
