"""Pure annotation logic. No I/O, no HTTP, no OpenCV, no pandas.

An individual's identity over a clip is an ordered list of SEGMENTS:

    [{"from": 0, "track_id": 41, "by": "tracker"}, {"from": 45, "track_id": 57, "by": "human"}]

meaning "this individual is track 41 until frame 45, then it is track 57". The first
entry's "from" is always 0. A "track_id" of None means "no box from here on".

Per-frame state is one character:

    r  the resting/baseline state (the default)
    a  the active state
    m  missed        the frame is inside the individual's span but there is no usable box
    -  no data       the frame is outside the individual's span entirely

The state vocabulary is configurable, see `states.py`. This module only cares that
there is exactly one baseline character, one or more painted characters, plus `m` and `-`.
"""

import copy

REST, ACTIVE, MISSED, NODATA = "r", "a", "m", "-"
PAINTABLE = (REST, ACTIVE, MISSED)
DISMISSED = ("merged", "discarded")


def iou(a, b):
    """Intersection over union of two [x1, y1, x2, y2] boxes. 0.0 for degenerate boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def active_track(segments, frame):
    """The track_id this individual follows at `frame`, or None."""
    tid = None
    for s in segments:
        if s["from"] <= frame:
            tid = s.get("track_id")
        else:
            break
    return tid


def resolve_boxes(segments, track_frames, n_frames):
    """Identity chain -> one box (or None) per frame.

    track_frames: {track_id: {frame_idx: [x1, y1, x2, y2]}}
    """
    out = [None] * n_frames
    for f in range(n_frames):
        tid = active_track(segments, f)
        if tid is not None:
            out[f] = track_frames.get(tid, {}).get(f)
    return out


def span(boxes):
    """(first, last) index of a non-None box, or (None, None) if there are none."""
    idx = [i for i, b in enumerate(boxes) if b is not None]
    return (idx[0], idx[-1]) if idx else (None, None)


def apply_geometry(fstate, boxes, paintable=PAINTABLE, rest=REST):
    """Force the geometry-determined characters, keep the human's paint everywhere else.

    Inside the span with no box -> 'm'. Inside the span with a box -> keep whatever
    paintable character is already there, promoting a stale '-' (left over from before a
    re-anchor gave this frame a box) back to the baseline default.

    Outside the span -> '-', UNLESS the human painted 'm' there. A track that dies at
    frame 42 does not mean the individual left the arena, and "the animal is there, the
    box is not" is precisely what 'm' records. Painted states are still refused outside
    the span, because judging a behavioural state is what a box is for. A row with no
    boxes at all keeps nothing: reconcile() merges it, and a merged row is not an entry.
    """
    n = len(boxes)
    fs = list((fstate or "").ljust(n, rest)[:n])
    lo, hi = span(boxes)
    for f in range(n):
        if lo is None or f < lo or f > hi:
            fs[f] = MISSED if (lo is not None and fs[f] == MISSED) else NODATA
        elif boxes[f] is None:
            fs[f] = MISSED
        elif fs[f] not in paintable:
            fs[f] = rest
    return "".join(fs)


def state_fracs(fstate, observable=(REST, ACTIVE)):
    """{state: share} over the frames where the individual was actually observable."""
    n = sum(fstate.count(c) for c in observable)
    if not n:
        return {c: 0.0 for c in observable}
    return {c: fstate.count(c) / n for c in observable}


def active_frac(fstate, active=ACTIVE, observable=(REST, ACTIVE)):
    """Active share of the frames where the individual was actually observable."""
    return state_fracs(fstate, observable).get(active, 0.0)


def bouts(fstate, fps, ch=ACTIVE):
    """Contiguous runs of `ch` as [{start, end, t0, t1}], end inclusive."""
    out, start = [], None
    for f, c in enumerate(fstate):
        if c == ch and start is None:
            start = f
        elif c != ch and start is not None:
            out.append({"start": start, "end": f - 1,
                        "t0": round(start / fps, 3), "t1": round((f - 1) / fps, 3)})
            start = None
    if start is not None:
        e = len(fstate) - 1
        out.append({"start": start, "end": e,
                    "t0": round(start / fps, 3), "t1": round(e / fps, 3)})
    return out


def paint_range(fstate, start, end, ch, backward=False):
    """Write `ch` over [start, end). A '-' is never written and stops the write.

    A '-' means the individual has no box in that frame, which is geometry and not
    judgement, so a drag can neither change one nor reach across one. `backward=True`
    scans from `end - 1` down to `start`, which is what a leftward boundary drag needs:
    the write has to start where the drag started, or the gap blocks the wrong side.
    """
    n = len(fstate)
    start, end = max(0, min(n, start)), max(0, min(n, end))
    if start >= end:
        return fstate
    out = list(fstate)
    order = range(end - 1, start - 1, -1) if backward else range(start, end)
    for f in order:
        if out[f] == NODATA:
            break
        out[f] = ch
    return "".join(out)


def boundaries(fstate):
    """Frames where a draggable state boundary sits: a change between two real states."""
    return [f for f in range(1, len(fstate))
            if fstate[f] != fstate[f - 1] and NODATA not in (fstate[f], fstate[f - 1])]


def countable(row, require_zone=False):
    """A row the grid counts as work: an individual that has not been dismissed.

    `merged` is the tool's own dismissal, decided by the ownership rules. `discarded` is
    the human's: not every tracker row is an animal, and a duplicate box on an already
    tracked individual or a one-frame fragment can neither be annotated nor allowed to
    block the clip forever. Neither is ever deleted from the file.

    `require_zone` is set when the project defines zones. A row with no zone is then an
    `auto_split` fragment or an individual outside every zone, and the grid has no column
    to draw it in. It stays in the file and stays visible in the video, so it can be
    re-anchored, but it is not counted as work.
    """
    if row.get("status") in DISMISSED:
        return False
    return bool(row.get("zone")) if require_zone else True


def overlay(rows, frame, require_zone=False):
    """Every box on screen at `frame`, one entry per distinct rectangle.

    The video is the identity surface: it must show every individual the tracker found,
    not only the rows the grid counts. Two kinds of row hold boxes the grid ignores, and
    hiding them is what makes an animal vanish mid-clip with nothing to click:

      * an `auto_split` fragment, which reconcile() creates with no zone whenever a
        re-anchor strands the tail of a track. That tail is a real animal on screen.
      * a `merged` row that still resolves to a box, e.g. a row whose taker was later
        re-anchored away.

    Entries are ordered countable-first, and a rectangle already claimed by an earlier
    entry is dropped, so a merged twin never double-strokes the row that absorbed it.

    Returns [{"individual_id", "box", "kind", "row"}] with kind "individual" for a
    countable row and "orphan" for a box no countable row owns.
    """
    def rank(r):                      # countable, then a live fragment, then a merged row
        if countable(r, require_zone):
            return 0
        return 1 if r.get("status") != "merged" else 2

    out, seen = [], set()
    for r in sorted(rows, key=rank):
        boxes = r.get("boxes") or []
        box = boxes[frame] if 0 <= frame < len(boxes) else None
        if box is None or tuple(box) in seen:
            continue
        seen.add(tuple(box))
        out.append({"individual_id": r["individual_id"], "box": box,
                    "kind": "individual" if countable(r, require_zone) else "orphan",
                    "row": r})
    return out


def _set_segment(segments, frame, track_id, by):
    """Insert or replace the segment starting at `frame`, dropping later ones it overrides."""
    kept = [s for s in segments if s["from"] < frame]
    if not kept:
        kept = [{"from": 0, "track_id": None, "by": "auto"}]
    kept.append({"from": frame, "track_id": track_id, "by": by})
    return kept


def _next_individual_id(rows):
    used = {int(r["individual_id"][1:]) for r in rows
            if r["individual_id"][1:].isdigit()}
    n = 0
    while n in used:
        n += 1
    return "i%02d" % n


def new_row(individual_id, seed_track_id, zone=None, source="tracker", segments=None):
    """A blank row. Derived fields are filled by reconcile()."""
    return {
        "individual_id": individual_id, "seed_track_id": seed_track_id, "zone": zone,
        "status": "unseen", "merged_into": None, "source": source,
        "needs_review": False, "note": "", "fstate": "",
        "segments": segments or [{"from": 0, "track_id": seed_track_id,
                                  "by": "tracker"}],
        "updated_at": None,
    }


def _refresh(row, track_frames, n_frames, fps, states=None):
    """Recompute every derived field of one row from its segments."""
    paintable = tuple(states["paintable"]) if states else PAINTABLE
    rest = states["rest"] if states else REST
    observable = tuple(states["observable"]) if states else (REST, ACTIVE)
    active = states["active"] if states else ACTIVE
    row["boxes"] = resolve_boxes(row["segments"], track_frames, n_frames)
    row["fstate"] = apply_geometry(row.get("fstate", ""), row["boxes"], paintable, rest)
    row["n_present"] = sum(1 for c in row["fstate"] if c in paintable)
    row["state_fracs"] = state_fracs(row["fstate"], observable)
    row["active_frac"] = row["state_fracs"].get(active, 0.0)
    row["bouts"] = bouts(row["fstate"], fps, active)
    return row


def reconcile(rows, track_frames, n_frames, fps, taker=None, states=None):
    """Enforce the ownership invariant and refresh every derived field.

    1. recompute each row's boxes and state from its segments
    2. a row left with no boxes at all becomes `merged` (into `taker`, when known)
    3. any box owned by nobody is adopted into a fresh `auto_split` row, one per
       contiguous run, so a corrected tracker error can never delete an individual
    """
    rows = copy.deepcopy(rows)

    for r in rows:
        _refresh(r, track_frames, n_frames, fps, states)

    for r in rows:
        if r["status"] in DISMISSED:       # a human `discarded` outranks the merge rule
            continue
        if all(b is None for b in r["boxes"]):
            r["status"] = "merged"
            r["merged_into"] = taker

    owned = set()
    for r in rows:
        if r["status"] == "merged":
            continue
        for f, b in enumerate(r["boxes"]):
            if b is not None:
                owned.add((active_track(r["segments"], f), f))

    for tid in sorted(track_frames):
        loose = sorted(f for f in track_frames[tid]
                       if f < n_frames and (tid, f) not in owned)
        run = []
        for f in loose + [None]:
            if run and (f is None or f != run[-1] + 1):
                segs = [{"from": 0, "track_id": None, "by": "auto"},
                        {"from": run[0], "track_id": tid, "by": "auto"}]
                if run[-1] + 1 < n_frames:
                    segs.append({"from": run[-1] + 1, "track_id": None, "by": "auto"})
                new = new_row(_next_individual_id(rows), tid, None, "auto_split", segs)
                rows.append(_refresh(new, track_frames, n_frames, fps, states))
                run = []
            if f is not None:
                run.append(f)

    return rows


def reanchor(rows, track_frames, n_frames, fps, individual_id, frame, track_id,
             states=None):
    """Give `individual_id` `track_id` from `frame` on, then restore the invariant.

    Whoever else held `track_id` at or after `frame` is truncated there.
    `individual_id`'s own previous track keeps whatever it had before `frame`; its tail
    is picked up by reconcile()'s auto-split.
    """
    rows = copy.deepcopy(rows)
    for r in rows:
        if r["individual_id"] == individual_id:
            r["segments"] = _set_segment(r["segments"], frame, track_id, "human")
        elif r["status"] != "merged" and any(
                active_track(r["segments"], f) == track_id
                for f in range(frame, n_frames)):
            r["segments"] = _set_segment(r["segments"], frame, None, "auto")
    return reconcile(rows, track_frames, n_frames, fps, taker=individual_id,
                     states=states)
