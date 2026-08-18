"""Labels out, in the three shapes an analysis actually wants.

  frames    one row per (clip, individual, frame). The complete record, and the only shape
            from which every other number can be recomputed. Large.
  bouts     one row per contiguous run of a painted state. The shape behaviour work wants,
            because a bout has a duration and a start time.
  units     one row per (clip, zone) with the fraction of observable frames in each state.
            This is the pen level activity fraction of the original study, the unit a
            method comparison is scored on.

Rows dismissed as `merged` are excluded from all three: they are the same individual
listed twice, not an individual. Rows the human `discarded` are excluded as well, but
counted in the report so the exclusion is visible rather than silent.
"""

import csv
import os

from . import core as cc
from . import store

FRAME_COLS = ["clip", "video", "individual_id", "track_id", "seed_track_id", "zone",
              "frame_idx", "t_sec", "state", "state_name", "x1", "y1", "x2", "y2",
              "status", "source", "needs_review", "clip_complete", "annotator"]

BOUT_COLS = ["clip", "video", "individual_id", "zone", "state", "state_name",
             "start_frame", "end_frame", "t0_sec", "t1_sec", "n_frames", "dur_sec",
             "clip_complete", "annotator"]

UNIT_COLS = ["clip", "video", "zone", "n_individuals", "observable_frames",
             "missed_frames", "clip_complete", "annotator"]


def _live_rows(doc):
    return [r for r in doc["individuals"] if r["status"] not in cc.DISMISSED]


def _state_names(doc, project):
    names = {s["key"]: s["name"] for s in (doc.get("states") or project.states.states)}
    names.setdefault(cc.MISSED, "missed")
    names.setdefault(cc.NODATA, "no data")
    return names


def _docs(project, complete_only=False):
    out = []
    for doc in store.load_all(project):
        if complete_only and not doc.get("complete"):
            continue
        out.append(doc)
    return out


def export_frames(project, out_path, complete_only=False):
    n = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FRAME_COLS)
        w.writeheader()
        for doc in _docs(project, complete_only):
            names = _state_names(doc, project)
            for r in _live_rows(doc):
                boxes = r.get("boxes") or [None] * doc["n_frames"]
                for i in range(doc["n_frames"]):
                    box = boxes[i] if i < len(boxes) else None
                    ch = r["fstate"][i] if i < len(r["fstate"]) else cc.NODATA
                    w.writerow({
                        "clip": doc["clip"], "video": doc.get("video", ""),
                        "individual_id": r["individual_id"],
                        "track_id": cc.active_track(r["segments"], i),
                        "seed_track_id": r["seed_track_id"], "zone": r.get("zone") or "",
                        "frame_idx": i, "t_sec": round(i / doc["fps"], 3),
                        "state": ch, "state_name": names.get(ch, ch),
                        "x1": box[0] if box else "", "y1": box[1] if box else "",
                        "x2": box[2] if box else "", "y2": box[3] if box else "",
                        "status": r["status"], "source": r["source"],
                        "needs_review": int(bool(r.get("needs_review"))),
                        "clip_complete": int(bool(doc.get("complete"))),
                        "annotator": doc.get("annotator", ""),
                    })
                    n += 1
    return n


def export_bouts(project, out_path, complete_only=False):
    n = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=BOUT_COLS)
        w.writeheader()
        for doc in _docs(project, complete_only):
            names = _state_names(doc, project)
            painted = [s["key"] for s in (doc.get("states") or project.states.states)
                       if not s.get("baseline")]
            for r in _live_rows(doc):
                for ch in painted + [cc.MISSED]:
                    for b in cc.bouts(r["fstate"], doc["fps"], ch):
                        n_fr = b["end"] - b["start"] + 1
                        w.writerow({
                            "clip": doc["clip"], "video": doc.get("video", ""),
                            "individual_id": r["individual_id"],
                            "zone": r.get("zone") or "", "state": ch,
                            "state_name": names.get(ch, ch),
                            "start_frame": b["start"], "end_frame": b["end"],
                            "t0_sec": b["t0"], "t1_sec": b["t1"], "n_frames": n_fr,
                            "dur_sec": round(n_fr / doc["fps"], 3),
                            "clip_complete": int(bool(doc.get("complete"))),
                            "annotator": doc.get("annotator", ""),
                        })
                        n += 1
    return n


def export_units(project, out_path, complete_only=False):
    """One row per (clip, zone). A zone-less project reports one row per clip."""
    keys = [s["key"] for s in project.states.states]
    cols = list(UNIT_COLS)
    for k in keys:
        cols += ["n_%s" % k, "frac_%s" % k]
    n = 0
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for doc in _docs(project, complete_only):
            by_zone = {}
            for r in _live_rows(doc):
                z = r.get("zone") or ""
                cell = by_zone.setdefault(z, {"n_ind": 0, "counts": {}, "missed": 0})
                cell["n_ind"] += 1
                cell["missed"] += r["fstate"].count(cc.MISSED)
                for k in keys:
                    cell["counts"][k] = cell["counts"].get(k, 0) + r["fstate"].count(k)
            for z, cell in sorted(by_zone.items()):
                obs = sum(cell["counts"].get(k, 0) for k in keys)
                row = {"clip": doc["clip"], "video": doc.get("video", ""), "zone": z,
                       "n_individuals": cell["n_ind"], "observable_frames": obs,
                       "missed_frames": cell["missed"],
                       "clip_complete": int(bool(doc.get("complete"))),
                       "annotator": doc.get("annotator", "")}
                for k in keys:
                    c = cell["counts"].get(k, 0)
                    row["n_%s" % k] = c
                    row["frac_%s" % k] = round(c / obs, 6) if obs else ""
                w.writerow(row)
                n += 1
    return n


WRITERS = {"frames": export_frames, "bouts": export_bouts, "units": export_units}


def export(project, shapes=("frames", "bouts", "units"), out_dir=None,
           complete_only=False, prefix=""):
    """Write the requested shapes into the project's exports directory. Returns a report."""
    out_dir = out_dir or project.exports_dir
    os.makedirs(out_dir, exist_ok=True)
    report = {}
    for shape in shapes:
        if shape not in WRITERS:
            raise ValueError("unknown export shape %r, expected one of %s"
                             % (shape, ", ".join(sorted(WRITERS))))
        path = os.path.join(out_dir, "%s%s.csv" % (prefix, shape))
        report[shape] = {"path": path, "rows": WRITERS[shape](project, path,
                                                              complete_only)}
    docs = _docs(project, complete_only)
    report["clips"] = len(docs)
    report["discarded_rows"] = sum(
        1 for d in docs for r in d["individuals"] if r["status"] == "discarded")
    report["merged_rows"] = sum(
        1 for d in docs for r in d["individuals"] if r["status"] == "merged")
    return report
