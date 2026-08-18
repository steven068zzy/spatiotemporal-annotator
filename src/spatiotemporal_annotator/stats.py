"""Corpus-wide progress, computed from the label files every time it is asked for.

A cached counter that can drift from the files it summarises is worse than a scan of a
few hundred small documents, so there is no cache. The panel exists to make a hole in the
sampling design visible while it can still be filled, which only works if it is true.

Grouping is driven by the tags a clip carries, so a corpus balanced on recording day and
camera reports by day and by camera without this module knowing what either is. Tags are
set at ingest with `sta add --tag week=3 --tag camera=cam1`.
"""

import os

from . import core as cc
from . import store

NO_ZONE = "?"


def bucket(doc):
    """'complete', 'started' or 'untouched'. A started clip has work in it, unfinished."""
    if doc.get("complete"):
        return "complete"
    live = store.countable(doc)
    if any(b.get("status") == "confirmed" for b in live):
        return "started"
    if any(b.get("status") == "discarded" or (b.get("fstate") or "").strip("r-")
           for b in doc.get("individuals", [])):
        return "started"
    return "untouched"


def _tags_of(project, clip_id):
    try:
        return project.clip_meta(clip_id).get("tags") or {}
    except (OSError, ValueError):
        return {}


def aggregate(project, docs=None):
    """The whole panel in one dict."""
    docs = store.load_all(project) if docs is None else docs
    states = project.states

    clips = {"complete": 0, "started": 0, "untouched": 0}
    frames = {"video": 0, "individual": 0}
    n_confirmed = 0
    zone = {}
    state_frames = {s["key"]: 0 for s in states.states}
    state_frames[cc.MISSED] = 0
    groups = {}

    known = set(project.clip_ids())
    for doc in docs:
        b = bucket(doc)
        clips[b] += 1
        live = store.countable(doc)
        confirmed = [r for r in live if r.get("status") == "confirmed"]
        n_confirmed += len(confirmed)

        for row in confirmed:
            key = row.get("zone") or NO_ZONE
            zone[key] = zone.get(key, 0) + 1

        if b == "complete":
            frames["video"] += doc.get("n_frames", 0)
            frames["individual"] += sum(r.get("n_present", 0) for r in confirmed)
            for row in confirmed:
                for ch in row.get("fstate") or "":
                    if ch in state_frames:
                        state_frames[ch] += 1

        if b == "untouched":
            continue
        if doc["clip"] in known:
            for k, v in _tags_of(project, doc["clip"]).items():
                g = groups.setdefault(k, {})
                cell = g.setdefault(str(v), {"complete": 0, "started": 0})
                cell[b if b in cell else "started"] += 1

    observable = sum(state_frames[s["key"]] for s in states.states)
    primary = state_frames.get(states.active, 0)

    return {
        "clips": clips,
        "n_clips_on_disk": len(known),
        "frames": frames,
        "individuals_confirmed": n_confirmed,
        "zone": [{"key": k, "n": v} for k, v in sorted(zone.items())],
        "state_frames": [{"key": s["key"], "name": s["name"], "color": s["color"],
                          "n": state_frames[s["key"]]} for s in states.states]
                        + [{"key": cc.MISSED, "name": "missed", "color": "#8a7d55",
                            "n": state_frames[cc.MISSED]}],
        "primary_state": states.active,
        "primary_fraction": (primary / observable) if observable else 0.0,
        "observable_frames": observable,
        "groups": [{"key": k, "rows": [{"key": kk, **vv}
                                       for kk, vv in sorted(v.items())]}
                   for k, v in sorted(groups.items())],
    }


def clip_table(project):
    """One row per clip on disk, for the queue dropdown and for `sta status`."""
    out = []
    for cid in project.clip_ids():
        doc = store.load(project, cid)
        st = (store.clip_status(doc) if doc
              else {"done": 0, "total": 0, "complete": False})
        meta = {}
        try:
            meta = project.clip_meta(cid)
        except (OSError, ValueError):
            pass
        out.append({"id": cid, "video": meta.get("video", ""),
                    "n_frames": meta.get("n_frames", 0),
                    "n_tracks": meta.get("n_tracks", 0),
                    "tags": meta.get("tags") or {},
                    "opened": doc is not None,
                    "skipped": project.is_skipped(cid), **st})
    return out


def format_status(project):
    """The text `sta status` prints."""
    rows = clip_table(project)
    agg = aggregate(project)
    lines = ["", "  project  %s" % project.config.get("name", ""),
             "  root     %s" % project.root,
             "  states   %s" % ", ".join(
                 "%s=%s" % (s["key"], s["name"]) for s in project.states.states),
             "  zones    %s" % (", ".join(project.zones.names) or "none"),
             "  mode     %s" % ("census, every row must be resolved"
                                if project.config.get("census_mode") else "free"),
             ""]
    lines.append("  %-34s %6s %6s %8s  %s" % ("clip", "done", "total", "frames", "tags"))
    for r in rows:
        tag = " ".join("%s=%s" % kv for kv in sorted(r["tags"].items()))
        mark = "SKIP" if r["skipped"] else ("done" if r["complete"] else "")
        lines.append("  %-34s %6d %6d %8d  %s %s"
                     % (r["id"], r["done"], r["total"], r["n_frames"], tag, mark))
    c = agg["clips"]
    lines += ["",
              "  %d clips complete, %d started, %d untouched"
              % (c["complete"], c["started"], c["untouched"]),
              "  %d individuals confirmed, %d labelled individual-frames"
              % (agg["individuals_confirmed"], agg["frames"]["individual"]),
              "  %s fraction over complete clips: %.4f  (%d observable frames)"
              % (project.states.name_of(agg["primary_state"]),
                 agg["primary_fraction"], agg["observable_frames"]),
              ""]
    return os.linesep.join(lines)
