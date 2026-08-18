"""A project is one directory. Everything the tool needs about a body of work is in it.

    myproject/
      project.yaml            the configuration below
      videos/                 the source videos, copied or linked in on `add`
      clips/<clip_id>/
        meta.json             frame count, size, fps, provenance of this clip
        frames/00000.jpg      one JPEG per annotated frame
        tracks.json           {track_id: {frame: box}} plus the zone of each track
      labels/<clip_id>.json   the annotation, one file per clip
      exports/                CSV written by `export`

Nothing outside the project directory is written, so a project is a self-contained unit
that can be zipped, moved between machines or archived beside a paper.

Frame rate and frame size are the two settings a new corpus most often has to change, so
both are explicit and both are recorded per clip rather than assumed globally. A project
holding a 30 fps GoPro clip and a 5 fps ceiling camera clip is a supported situation.
"""

import copy
import json
import os
import re

from .states import States
from .zones import Zones

CONFIG_NAME = "project.yaml"
CLIP_ID_RE = re.compile(r"^[A-Za-z0-9][\w\-.]*$")

DEFAULTS = {
    "name": "untitled project",
    # ---- frame rate -----------------------------------------------------------------
    # extract_fps  resample the video to this rate on ingest. null keeps every frame.
    #              A 30 fps recording annotated at 5 fps costs a sixth of the clicks and
    #              loses nothing for behaviour that lasts longer than a fifth of a second.
    # playback_fps the rate the browser plays at, and the speeds offered in the dropdown.
    "extract_fps": None,
    "playback_fps": 5,
    "playback_speeds": [1, 2, 3, 5, 8, 10, 15, 25, 30],
    # ---- frame size -----------------------------------------------------------------
    # frame_max_width  downscale frames on extraction to at most this many pixels wide.
    #                  Boxes and zones are scaled with them, so nothing else changes.
    # display_max_scale  how far the browser may enlarge the frame to fill its column.
    "frame_max_width": None,
    "jpeg_quality": 85,
    "display_max_scale": 2.5,
    # ---- how much of a video to annotate --------------------------------------------
    # null annotates the whole clip. A number caps it, which is how the original study
    # held every clip to a 20 s window.
    "window_frames": None,
    # ---- detection ------------------------------------------------------------------
    "detector": {
        "model": None,          # path to a .pt, or null to require precomputed detections
        "conf": 0.25,
        "iou": 0.45,
        "imgsz": 640,
        "classes": None,        # keep only these class indices, null keeps all
        "min_box_px": 0,        # drop boxes smaller than this on a side
        "max_box_px": None,     # drop boxes larger than this on a side
        "device": None,         # null lets the backend choose
        "batch": 16,
    },
    # ---- association ----------------------------------------------------------------
    "tracker": {
        "kind": "simple",       # simple | bytetrack
        "iou_match": 0.3,
        "max_age": 30,          # frames a track survives unmatched
        "min_hits": 1,
        "bytetrack_yaml": None,  # used when kind = bytetrack
    },
    # ---- annotation -----------------------------------------------------------------
    "states": None,             # null uses the resting/active default
    "zones": [],
    # census_mode refuses to mark a clip complete while any row is unresolved, which is
    # what a reference set needs. Off by default, so a user labelling two animals out of
    # forty is not blocked by the other thirty-eight.
    "census_mode": False,
    "annotator": "",
}


class ProjectError(RuntimeError):
    pass


def _deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _dump_yaml(obj, path):
    try:
        import yaml
    except ImportError:
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
        return
    with open(path, "w") as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=True)


def _load_yaml(path):
    with open(path) as f:
        text = f.read()
    try:
        import yaml
    except ImportError:
        return json.loads(text)
    return yaml.safe_load(text) or {}


class Project:
    def __init__(self, root, config=None):
        self.root = os.path.abspath(root)
        self.config = _deep_merge(DEFAULTS, config or {})
        self.states = States(self.config.get("states"))
        self.zones = Zones(self.config.get("zones"))

    # ---- lifecycle ----------------------------------------------------------------
    @classmethod
    def create(cls, root, **over):
        root = os.path.abspath(root)
        cfg_path = os.path.join(root, CONFIG_NAME)
        if os.path.exists(cfg_path):
            raise ProjectError("a project already exists at %s" % root)
        p = cls(root, over)
        for d in (p.videos_dir, p.clips_dir, p.labels_dir, p.exports_dir):
            os.makedirs(d, exist_ok=True)
        p.save()
        return p

    @classmethod
    def load(cls, root):
        root = os.path.abspath(root)
        cfg_path = os.path.join(root, CONFIG_NAME)
        if not os.path.exists(cfg_path):
            raise ProjectError(
                "no %s in %s. Run `sta init %s` to create one."
                % (CONFIG_NAME, root, root))
        return cls(root, _load_yaml(cfg_path))

    @classmethod
    def load_or_create(cls, root, **over):
        try:
            return cls.load(root)
        except ProjectError:
            return cls.create(root, **over)

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        _dump_yaml(self.config, os.path.join(self.root, CONFIG_NAME))

    # ---- layout -------------------------------------------------------------------
    @property
    def videos_dir(self):
        return os.path.join(self.root, "videos")

    @property
    def clips_dir(self):
        return os.path.join(self.root, "clips")

    @property
    def labels_dir(self):
        return os.path.join(self.root, "labels")

    @property
    def exports_dir(self):
        return os.path.join(self.root, "exports")

    def clip_dir(self, clip_id):
        return os.path.join(self.clips_dir, self.check_clip_id(clip_id))

    def frames_dir(self, clip_id):
        return os.path.join(self.clip_dir(clip_id), "frames")

    def frame_path(self, clip_id, idx):
        return os.path.join(self.frames_dir(clip_id), "%05d.jpg" % int(idx))

    def clip_meta_path(self, clip_id):
        return os.path.join(self.clip_dir(clip_id), "meta.json")

    def tracks_path(self, clip_id):
        return os.path.join(self.clip_dir(clip_id), "tracks.json")

    def label_path(self, clip_id):
        return os.path.join(self.labels_dir, "%s.json" % self.check_clip_id(clip_id))

    def skip_path(self, clip_id):
        return os.path.join(self.labels_dir,
                            "%s__SKIP.json" % self.check_clip_id(clip_id))

    @staticmethod
    def check_clip_id(clip_id):
        """A clip id becomes a path component, so it is validated, never sanitised."""
        if not CLIP_ID_RE.match(str(clip_id)):
            raise ProjectError("refusing suspicious clip id: %r" % (clip_id,))
        return str(clip_id)

    # ---- clips --------------------------------------------------------------------
    def clip_ids(self):
        if not os.path.isdir(self.clips_dir):
            return []
        out = []
        for name in sorted(os.listdir(self.clips_dir)):
            if os.path.exists(os.path.join(self.clips_dir, name, "meta.json")):
                out.append(name)
        return out

    def clip_meta(self, clip_id):
        with open(self.clip_meta_path(clip_id)) as f:
            return json.load(f)

    def has_clip(self, clip_id):
        return os.path.exists(self.clip_meta_path(clip_id))

    def is_skipped(self, clip_id):
        return os.path.exists(self.skip_path(clip_id))

    def load_tracks(self, clip_id):
        """({track_id: {frame: box}}, {track_id: zone}). JSON keys are strings."""
        with open(self.tracks_path(clip_id)) as f:
            d = json.load(f)
        frames = {int(t): {int(f): list(b) for f, b in v.items()}
                  for t, v in d["tracks"].items()}
        zones = {int(t): z for t, z in (d.get("zones") or {}).items()}
        return frames, zones

    def save_tracks(self, clip_id, frames, zones):
        os.makedirs(self.clip_dir(clip_id), exist_ok=True)
        payload = {"tracks": {str(t): {str(f): list(b) for f, b in v.items()}
                              for t, v in frames.items()},
                   "zones": {str(t): z for t, z in zones.items()}}
        tmp = self.tracks_path(clip_id) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, self.tracks_path(clip_id))

    # ---- settings the browser needs ----------------------------------------------
    def ui_config(self):
        return {
            "name": self.config["name"],
            "states": self.states.as_dict(),
            "zones": self.zones.names,
            "playback_fps": self.config["playback_fps"],
            "playback_speeds": self.config["playback_speeds"],
            "display_max_scale": self.config["display_max_scale"],
            "census_mode": bool(self.config["census_mode"]),
            # Whether a row needs a zone is a property of each CLIP, not of the project,
            # because `sta add --zones-file` can give zones to some clips and not others.
            # The browser reads it off the clip document.
            "annotator": self.config.get("annotator", ""),
            "has_model": bool((self.config.get("detector") or {}).get("model")),
        }
