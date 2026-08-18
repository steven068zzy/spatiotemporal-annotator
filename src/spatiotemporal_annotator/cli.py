"""Command line entry point: sta init | add | serve | export | status | demo."""

import argparse
import glob
import json
import os
import shutil
import sys

from . import __version__
from .export import export as export_labels
from . import ingest as ingest_mod
from . import server as server_mod
from . import stats as stats_mod
from .project import Project, ProjectError
from .states import StateConfigError
from .zones import ZoneConfigError

EXAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "examples")


def _parse_states(specs):
    """--state r:resting:baseline --state a:active:primary  ->  the states list."""
    if not specs:
        return None
    out = []
    for s in specs:
        parts = s.split(":")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            raise SystemExit("--state wants key:name[:baseline|:primary][:#rrggbb], "
                             "got %r" % s)
        st = {"key": parts[0], "name": parts[1]}
        for flag in parts[2:]:
            if flag == "baseline":
                st["baseline"] = True
            elif flag == "primary":
                st["primary"] = True
            elif flag.startswith("#"):
                st["color"] = flag
            else:
                raise SystemExit("unknown --state flag %r in %r" % (flag, s))
        out.append(st)
    return out


def _parse_zones(specs, zones_file):
    if zones_file:
        with open(zones_file) as f:
            text = f.read()
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError:
            data = json.loads(text)
        return data.get("zones", data) if isinstance(data, dict) else data
    out = []
    for s in specs or []:
        name, _, rect = s.partition(":")
        nums = [float(v) for v in rect.replace(" ", "").split(",") if v != ""]
        if not name or len(nums) != 4:
            raise SystemExit("--zone wants name:x1,y1,x2,y2, got %r" % s)
        out.append({"name": name, "rect": nums})
    return out


def _parse_tags(specs):
    tags = {}
    for s in specs or []:
        k, sep, v = s.partition("=")
        if not sep or not k:
            raise SystemExit("--tag wants key=value, got %r" % s)
        tags[k] = v
    return tags


def _echo(msg):
    print("    %s" % msg, flush=True)


# ---- commands ---------------------------------------------------------------------
def cmd_init(a):
    over = {"name": a.name or os.path.basename(os.path.abspath(a.project))}
    if a.extract_fps is not None:
        over["extract_fps"] = a.extract_fps
    if a.playback_fps is not None:
        over["playback_fps"] = a.playback_fps
    if a.frame_max_width is not None:
        over["frame_max_width"] = a.frame_max_width
    if a.window_frames is not None:
        over["window_frames"] = a.window_frames
    if a.census_mode:
        over["census_mode"] = True
    if a.annotator:
        over["annotator"] = a.annotator
    states = _parse_states(a.state)
    if states:
        over["states"] = states
    zones = _parse_zones(a.zone, a.zones_file)
    if zones:
        over["zones"] = zones
    if a.model:
        over["detector"] = {"model": os.path.abspath(os.path.expanduser(a.model))}
    if a.tracker:
        over.setdefault("tracker", {})["kind"] = a.tracker
    p = Project.create(a.project, **over)
    print("\n  created %s" % p.root)
    print("  states  %s" % ", ".join("%s = %s" % (s["key"], s["name"])
                                     for s in p.states.states))
    print("  zones   %s" % (", ".join(p.zones.names) or "none, a single column"))
    print("\n  next:  sta add %s /path/to/video.mp4\n" % p.root)
    return 0


def cmd_add(a):
    p = Project.load(a.project)
    videos = []
    for pat in a.videos:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        videos.extend(hits or [pat])
    if not videos:
        raise SystemExit("no videos matched")
    tags = _parse_tags(a.tag)
    zones = None
    if a.zones_file:
        from .zones import Zones
        zones = Zones(_parse_zones(None, a.zones_file))
        _echo("zones for these clips: %s" % ", ".join(zones.names))
    done, failed = ingest_mod.ingest_many(
        p, videos, prefix=a.prefix, detections=a.detections, overwrite=a.overwrite,
        copy_video=not a.no_copy, progress=_echo, zones=zones, clip_id=a.clip)
    if tags:
        for meta in done:
            meta["tags"] = tags
            with open(p.clip_meta_path(meta["clip"]), "w") as f:
                json.dump(meta, f, indent=2)
    print("\n  %d clip(s) added, %d failed" % (len(done), len(failed)))
    for f in failed:
        print("    %s: %s" % (f["clip"], f["error"]))
    if done:
        print("\n  next:  sta serve %s\n" % p.root)
    return 1 if failed and not done else 0


def cmd_serve(a):
    server_mod.serve(a.project, annotator=a.annotator, port=a.port, host=a.host)
    return 0


def cmd_export(a):
    p = Project.load(a.project)
    rep = export_labels(p, a.shapes, out_dir=a.out_dir,
                        complete_only=a.complete_only, prefix=a.prefix)
    print("\n  %d clip(s) exported" % rep["clips"])
    for shape in a.shapes:
        print("    %-7s %8d rows -> %s"
              % (shape, rep[shape]["rows"], rep[shape]["path"]))
    print("    excluded: %d merged row(s), %d discarded row(s)"
          % (rep["merged_rows"], rep["discarded_rows"]))
    if rep.get("zoneless_rows"):
        print("    %d row(s) have no zone, so they are in frames and bouts but in no "
              "unit" % rep["zoneless_rows"])
    print("")
    return 0


def cmd_status(a):
    print(stats_mod.format_status(Project.load(a.project)))
    return 0


def cmd_demo(a):
    """Build a project from the bundled examples and serve it.

    The examples ship their detections, so this runs with no model, no GPU and no
    network. It is the fastest way to see what a label that carries a duration is.

    They also ship the study's own annotation, and it is loaded by default, so the demo
    opens on finished work rather than on 39 unlabelled rows. `--blank` leaves the clips
    unannotated for anyone who would rather do it themselves. An existing label file is
    never overwritten either way, so a demo project that has been worked in survives
    a second `sta demo`.
    """
    root = os.path.abspath(a.project or "demo-project")
    src = a.examples or EXAMPLES_DIR
    manifest_path = os.path.join(src, "examples.json")
    if not os.path.isfile(manifest_path):
        raise SystemExit(
            "no examples manifest at %s. Pass --examples with the directory holding "
            "examples.json." % manifest_path)
    with open(manifest_path) as f:
        manifest = json.load(f)

    if os.path.exists(os.path.join(root, "project.yaml")):
        p = Project.load(root)
        print("\n  reusing the existing demo project at %s" % root)
    else:
        cfg = dict(manifest.get("project") or {})
        cfg["name"] = cfg.get("name", "demo")
        p = Project.create(root, **cfg)
        print("\n  created %s" % root)

    for ex in manifest["clips"]:
        cid = ex["clip"]
        if p.has_clip(cid) and not a.overwrite:
            print("    %s already ingested" % cid)
            continue
        video = os.path.join(src, ex["video"])
        dets = os.path.join(src, ex["detections"])
        if not os.path.isfile(video):
            print("    %s SKIPPED, no video at %s" % (cid, video))
            continue
        print("    ingesting %s" % cid)
        zones = None
        if ex.get("zones"):
            from .zones import Zones
            zones = Zones(ex["zones"])
        meta = ingest_mod.ingest_video(p, video, cid, dets, overwrite=a.overwrite,
                                       copy_video=True, progress=_echo, zones=zones)
        if ex.get("tags"):
            meta["tags"] = ex["tags"]
            with open(p.clip_meta_path(cid), "w") as f:
                json.dump(meta, f, indent=2)

    # Labels are copied in their own pass, after ingestion, so that re-running `sta demo`
    # over a project built by an earlier version still picks them up. A clip that was
    # skipped by the `already ingested` guard above never reaches the loop body.
    if not a.blank:
        loaded = kept = 0
        for ex in manifest["clips"]:
            if not ex.get("labels") or not p.has_clip(ex["clip"]):
                continue
            src_lab = os.path.join(src, ex["labels"])
            dst_lab = p.label_path(ex["clip"])
            if os.path.exists(dst_lab):
                kept += 1                     # never clobber work already in the project
            elif os.path.isfile(src_lab):
                os.makedirs(p.labels_dir, exist_ok=True)
                shutil.copyfile(src_lab, dst_lab)
                loaded += 1
        if loaded:
            print("    loaded the study's annotation for %d clip(s)" % loaded)
        if kept:
            print("    kept the annotation already in this project for %d clip(s)" % kept)

    if a.no_serve:
        print("\n  ready. Run:  sta serve %s\n" % root)
        return 0
    server_mod.serve(root, annotator=a.annotator or "demo", port=a.port, host=a.host)
    return 0


# ---- parser -----------------------------------------------------------------------
def build_parser():
    ap = argparse.ArgumentParser(
        prog="sta", description="Spatiotemporal annotation: every label carries an "
                                "identity and a duration.")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="create a project directory")
    i.add_argument("project")
    i.add_argument("--name")
    i.add_argument("--extract-fps", type=float,
                   help="resample video to this rate on ingest, e.g. 5 for a 30 fps file")
    i.add_argument("--playback-fps", type=float, help="default browser playback rate")
    i.add_argument("--frame-max-width", type=int,
                   help="downscale frames to at most this many pixels wide")
    i.add_argument("--window-frames", type=int,
                   help="annotate only the first N frames of each clip")
    i.add_argument("--model", help="detector checkpoint, any Ultralytics .pt")
    i.add_argument("--tracker", choices=["simple", "bytetrack"])
    i.add_argument("--state", action="append",
                   help="key:name[:baseline|:primary][:#rrggbb], repeatable")
    i.add_argument("--zone", action="append", help="name:x1,y1,x2,y2, repeatable")
    i.add_argument("--zones-file", help="YAML or JSON file holding a zones list")
    i.add_argument("--census-mode", action="store_true",
                   help="refuse to mark a clip complete while a row is unresolved")
    i.add_argument("--annotator", default="")
    i.set_defaults(func=cmd_init)

    d = sub.add_parser("add", help="ingest one or more videos into a project")
    d.add_argument("project")
    d.add_argument("videos", nargs="+")
    d.add_argument("--clip", help="clip id, only valid with a single video")
    d.add_argument("--prefix", help="prepended to every generated clip id")
    d.add_argument("--detections",
                   help="use these detections instead of running the model")
    d.add_argument("--tag", action="append", help="key=value, repeatable")
    d.add_argument("--zones-file",
                   help="zones for these clips only, overriding the project's. Use this "
                        "when several cameras see the same enclosures at different pixels.")
    d.add_argument("--overwrite", action="store_true")
    d.add_argument("--no-copy", action="store_true",
                   help="read the video in place instead of copying it in")
    d.set_defaults(func=cmd_add)

    s = sub.add_parser("serve", help="open the annotation interface")
    s.add_argument("project")
    s.add_argument("--port", type=int, default=8767)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--annotator", default="")
    s.set_defaults(func=cmd_serve)

    e = sub.add_parser("export", help="write CSV")
    e.add_argument("project")
    e.add_argument("--shapes", nargs="+", default=["frames", "bouts", "units"],
                   choices=["frames", "bouts", "units"])
    e.add_argument("--complete-only", action="store_true")
    e.add_argument("--out-dir")
    e.add_argument("--prefix", default="")
    e.set_defaults(func=cmd_export)

    t = sub.add_parser("status", help="what is annotated and what is not")
    t.add_argument("project")
    t.set_defaults(func=cmd_status)

    m = sub.add_parser("demo", help="build and serve a project from the bundled examples")
    m.add_argument("project", nargs="?", default="demo-project")
    m.add_argument("--examples", help="directory holding examples.json")
    m.add_argument("--port", type=int, default=8767)
    m.add_argument("--host", default="127.0.0.1")
    m.add_argument("--annotator", default="")
    m.add_argument("--overwrite", action="store_true")
    m.add_argument("--blank", action="store_true",
                   help="do not load the bundled annotation, start empty")
    m.add_argument("--no-serve", action="store_true")
    m.set_defaults(func=cmd_demo)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    if getattr(a, "clip", None) and len(getattr(a, "videos", [])) > 1:
        raise SystemExit("--clip names one clip, so it takes a single video")
    try:
        return a.func(a)
    except (ProjectError, StateConfigError, ZoneConfigError,
            ingest_mod.IngestError) as e:
        print("\n  error: %s\n" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
