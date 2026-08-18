"""Association: per-frame boxes in, one identity per animal out.

The tracker only has to be good enough to propose. The human has the last word, and one
click re-anchors a row onto the correct animal from that frame on, so an identity switch
costs a click rather than a lost individual. That is why the default is a small,
dependency free IoU tracker instead of something that needs a GPU.

  SimpleTracker    greedy IoU matching with a coast buffer. Pure Python, no dependencies.
  ByteTrackAdapter Ultralytics ByteTrack, for a corpus where association quality matters
                   and the extra dependency is acceptable.

Both return `{track_id: {frame_idx: [x1, y1, x2, y2]}}`.

A note that cost the original study a re-extraction: Ultralytics reinterprets
`track_buffer` in units of `frame_rate`, so a tracker configured for 30 fps and run on a
5 fps clip coasts six times too long. `ByteTrackAdapter` passes the clip's real rate.
"""

from .core import iou


def _greedy_match(tracks, dets, thresh):
    """[(track_index, det_index)] highest IoU first, each side used at most once."""
    pairs = []
    for ti, t in enumerate(tracks):
        for di, d in enumerate(dets):
            v = iou(t, d)
            if v >= thresh:
                pairs.append((v, ti, di))
    pairs.sort(reverse=True)
    used_t, used_d, out = set(), set(), []
    for _, ti, di in pairs:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        out.append((ti, di))
    return out


class SimpleTracker:
    """Greedy IoU association with a coast buffer.

    A track keeps its last box while unmatched, for at most `max_age` frames, which
    bridges a short occlusion without inventing motion. Nothing is extrapolated: a
    coasting track holds still, so the annotator sees a stale box rather than a
    confident wrong one, and `m` is the honest label for those frames.
    """

    def __init__(self, iou_match=0.3, max_age=30, min_hits=1):
        self.iou_match = float(iou_match)
        self.max_age = int(max_age)
        self.min_hits = max(1, int(min_hits))

    def run(self, dets_per_frame):
        live = []          # [{"id", "box", "age", "hits", "frames": {f: box}}]
        done = []
        next_id = 1
        for f, dets in enumerate(dets_per_frame):
            boxes = [d[:4] for d in dets]
            matched = _greedy_match([t["box"] for t in live], boxes, self.iou_match)
            hit_t = {ti for ti, _ in matched}
            for ti, di in matched:
                t = live[ti]
                t["box"] = boxes[di]
                t["frames"][f] = boxes[di]
                t["age"] = 0
                t["hits"] += 1
            for ti, t in enumerate(live):
                if ti not in hit_t:
                    t["age"] += 1
            hit_d = {di for _, di in matched}
            for di, box in enumerate(boxes):
                if di in hit_d:
                    continue
                live.append({"id": next_id, "box": box, "age": 0, "hits": 1,
                             "frames": {f: box}})
                next_id += 1
            still = []
            for t in live:
                if t["age"] > self.max_age:
                    done.append(t)
                else:
                    still.append(t)
            live = still
        done.extend(live)
        return {t["id"]: t["frames"] for t in done if t["hits"] >= self.min_hits}

    def describe(self):
        return {"kind": "simple", "iou_match": self.iou_match,
                "max_age": self.max_age, "min_hits": self.min_hits}


class ByteTrackAdapter:
    """Ultralytics ByteTrack. Needs `ultralytics`, and a frame rate that is not a guess."""

    def __init__(self, fps, yaml_path=None):
        self.fps = float(fps) or 5.0
        self.yaml_path = yaml_path
        try:
            from ultralytics.trackers.byte_tracker import BYTETracker
            from ultralytics.utils import IterableSimpleNamespace, yaml_load
        except ImportError as e:
            raise RuntimeError(
                "tracker.kind = bytetrack needs the ultralytics package. Install it, or "
                "use the default tracker.kind = simple.") from e
        cfg = dict(track_high_thresh=0.25, track_low_thresh=0.1, new_track_thresh=0.25,
                   track_buffer=30, match_thresh=0.8, fuse_score=False,
                   gmc_method=None)
        if yaml_path:
            cfg.update({k: v for k, v in (yaml_load(yaml_path) or {}).items()})
        self._ns = IterableSimpleNamespace(**cfg)
        self._BYTETracker = BYTETracker

    def run(self, dets_per_frame):
        import numpy as np

        class _Res:
            """The minimal duck type BYTETracker.update reads."""

            def __init__(self, arr):
                self.xyxy = arr[:, :4]
                self.conf = arr[:, 4]
                self.cls = arr[:, 5]
                # ByteTrack returns the class column untouched, so the detection row
                # index is smuggled through it to recover the exact association.
                self.data = arr

            def __len__(self):
                return len(self.xyxy)

        tr = self._BYTETracker(self._ns, frame_rate=int(round(self.fps)))
        out = {}
        for f, dets in enumerate(dets_per_frame):
            if dets:
                arr = np.array([[d[0], d[1], d[2], d[3], d[4], i]
                                for i, d in enumerate(dets)], dtype=float)
            else:
                arr = np.zeros((0, 6), dtype=float)
            res = tr.update(_Res(arr))
            for row in res:
                tid = int(row[4])
                di = int(row[6]) if len(row) > 6 else None
                box = (dets[di][:4] if di is not None and di < len(dets)
                       else [float(v) for v in row[:4]])
                out.setdefault(tid, {})[f] = [float(v) for v in box]
        return out

    def describe(self):
        return {"kind": "bytetrack", "fps": self.fps, "yaml": self.yaml_path}


def from_config(cfg, fps):
    cfg = dict(cfg or {})
    kind = (cfg.get("kind") or "simple").lower()
    if kind == "bytetrack":
        return ByteTrackAdapter(fps, cfg.get("bytetrack_yaml"))
    if kind != "simple":
        raise ValueError("unknown tracker.kind %r, expected 'simple' or 'bytetrack'"
                         % kind)
    return SimpleTracker(cfg.get("iou_match", 0.3), cfg.get("max_age", 30),
                         cfg.get("min_hits", 1))


def tracks_from_ids(dets_per_frame, ids_per_frame):
    """Identities a detections file already carried, no association run."""
    out = {}
    for f, (dets, ids) in enumerate(zip(dets_per_frame, ids_per_frame)):
        for d, tid in zip(dets, ids):
            if tid is None:
                continue
            out.setdefault(int(tid), {})[f] = [float(v) for v in d[:4]]
    return out
