"""Detection backends. One interface, so the annotator never knows which one ran.

A backend takes a list of frames as numpy arrays and returns, per frame, a list of
detections `[x1, y1, x2, y2, conf, cls]` in the coordinates of the frame it was given.

Three backends ship:

  UltralyticsDetector   any Ultralytics checkpoint, which is how a user brings their own
                        model. YOLOv8 through YOLO26 and RT-DETR all load the same way.
  PrecomputedDetections a file of detections produced elsewhere. This is what the bundled
                        examples use, so they run with no model and no GPU, and it is also
                        the escape hatch for a detector this module does not know about.
  NullDetector          refuses politely, so a project with no model configured fails with
                        a sentence instead of an AttributeError.

Adding a backend means one class with a `run(frames, offset)` method and one line in
`from_config`.
"""

import csv
import json
import os


class DetectorError(RuntimeError):
    pass


class Detector:
    """Interface. `run` returns a list, one entry per input frame."""

    name = "detector"

    def run(self, frames, offset=0):
        raise NotImplementedError

    def describe(self):
        return {"backend": self.name}


class NullDetector(Detector):
    name = "none"

    def run(self, frames, offset=0):
        raise DetectorError(
            "this project has no detector configured. Either set detector.model in "
            "project.yaml to a checkpoint path, or pass --detections with a file of "
            "boxes produced elsewhere.")


class PrecomputedDetections(Detector):
    """Detections read from a file. Accepts either shape:

    JSON   {"frames": {"0": [[x1, y1, x2, y2, conf, cls], ...], "1": [...]}}
           or the flat list [{"frame": 0, "x1": .., "y1": .., "x2": .., "y2": ..}, ...]
    CSV    a header with frame, x1, y1, x2, y2 and optionally conf, cls, track_id

    A `track_id` column is kept and, when present, lets `ingest` skip association
    altogether and use the identities the file already carries.
    """

    name = "precomputed"

    def __init__(self, path):
        self.path = os.path.abspath(path)
        if not os.path.exists(self.path):
            raise DetectorError("no detections file at %s" % self.path)
        self.by_frame, self.tracks_by_frame = self._read(self.path)
        # A frame with detections but no track_id column stores [None, None, ...], and a
        # list of Nones is truthy, so the test has to look at the ids themselves.
        self.has_tracks = any(t is not None
                              for ids in self.tracks_by_frame.values() for t in ids)

    @staticmethod
    def _read(path):
        by_frame, tracks = {}, {}

        def add(f, box, tid):
            by_frame.setdefault(int(f), []).append(
                [float(box[0]), float(box[1]), float(box[2]), float(box[3]),
                 float(box[4]) if len(box) > 4 and box[4] != "" else 1.0,
                 int(float(box[5])) if len(box) > 5 and box[5] != "" else 0])
            tracks.setdefault(int(f), []).append(
                int(float(tid)) if tid not in (None, "") else None)

        if path.lower().endswith(".json"):
            with open(path) as fh:
                d = json.load(fh)
            if isinstance(d, dict) and "frames" in d:
                for f, dets in d["frames"].items():
                    for row in dets:
                        if isinstance(row, dict):
                            add(f, [row["x1"], row["y1"], row["x2"], row["y2"],
                                    row.get("conf", 1.0), row.get("cls", 0)],
                                row.get("track_id"))
                        else:
                            add(f, row, row[6] if len(row) > 6 else None)
            elif isinstance(d, list):
                for row in d:
                    add(row["frame"], [row["x1"], row["y1"], row["x2"], row["y2"],
                                       row.get("conf", 1.0), row.get("cls", 0)],
                        row.get("track_id"))
            else:
                raise DetectorError(
                    "unrecognised JSON detections in %s. Expected {'frames': {...}} "
                    "or a list of per-detection objects." % path)
        else:
            with open(path, newline="") as fh:
                rd = csv.DictReader(fh)
                need = {"frame", "x1", "y1", "x2", "y2"}
                missing = need - set(rd.fieldnames or [])
                if missing:
                    raise DetectorError(
                        "detections CSV %s is missing column(s) %s"
                        % (path, ", ".join(sorted(missing))))
                for row in rd:
                    add(row["frame"], [row["x1"], row["y1"], row["x2"], row["y2"],
                                       row.get("conf", ""), row.get("cls", "")],
                        row.get("track_id"))
        return by_frame, tracks

    def run(self, frames, offset=0):
        return [self.by_frame.get(offset + i, []) for i in range(len(frames))]

    def track_ids(self, offset, count):
        return [self.tracks_by_frame.get(offset + i, []) for i in range(count)]

    def describe(self):
        return {"backend": self.name, "path": self.path,
                "n_frames_with_boxes": len(self.by_frame),
                "carries_track_ids": self.has_tracks}


class UltralyticsDetector(Detector):
    """Any Ultralytics checkpoint. This is the path for a user supplied model."""

    name = "ultralytics"

    def __init__(self, model, conf=0.25, iou=0.45, imgsz=640, classes=None,
                 device=None, batch=16):
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise DetectorError(
                "the ultralytics package is needed to run a checkpoint. Install it with "
                "`pip install 'spatiotemporal-annotator[detect]'`, or supply detections "
                "from your own pipeline with --detections.") from e
        model = os.path.expanduser(str(model))
        if not os.path.exists(model):
            raise DetectorError("no model checkpoint at %s" % model)
        self.model_path = os.path.abspath(model)
        self.model = YOLO(self.model_path)
        self.conf, self.iou, self.imgsz = conf, iou, imgsz
        self.classes, self.device, self.batch = classes, device, max(1, int(batch))

    def run(self, frames, offset=0):
        out = []
        for i in range(0, len(frames), self.batch):
            chunk = frames[i:i + self.batch]
            kw = dict(conf=self.conf, iou=self.iou, imgsz=self.imgsz, verbose=False)
            if self.classes is not None:
                kw["classes"] = list(self.classes)
            if self.device is not None:
                kw["device"] = self.device
            for res in self.model.predict(chunk, **kw):
                dets = []
                b = getattr(res, "boxes", None)
                if b is not None and len(b):
                    xyxy = b.xyxy.tolist()
                    confs = b.conf.tolist() if b.conf is not None else [1.0] * len(xyxy)
                    clss = b.cls.tolist() if b.cls is not None else [0] * len(xyxy)
                    for k, xy in enumerate(xyxy):
                        dets.append([float(xy[0]), float(xy[1]), float(xy[2]),
                                     float(xy[3]), float(confs[k]), int(clss[k])])
                out.append(dets)
        return out

    def describe(self):
        return {"backend": self.name, "model": self.model_path, "conf": self.conf,
                "iou": self.iou, "imgsz": self.imgsz, "classes": self.classes}


def filter_boxes(dets, min_side=0, max_side=None):
    """Drop boxes outside the size range. A degenerate box is always dropped."""
    out = []
    for d in dets:
        w, h = d[2] - d[0], d[3] - d[1]
        if w <= 0 or h <= 0:
            continue
        if min_side and (w < min_side or h < min_side):
            continue
        if max_side and (w > max_side or h > max_side):
            continue
        out.append(d)
    return out


def from_config(detector_cfg, detections_path=None):
    """Build the backend a project asks for. An explicit file always wins."""
    cfg = dict(detector_cfg or {})
    if detections_path:
        return PrecomputedDetections(detections_path)
    if cfg.get("model"):
        return UltralyticsDetector(
            cfg["model"], conf=cfg.get("conf", 0.25), iou=cfg.get("iou", 0.45),
            imgsz=cfg.get("imgsz", 640), classes=cfg.get("classes"),
            device=cfg.get("device"), batch=cfg.get("batch", 16))
    return NullDetector()
