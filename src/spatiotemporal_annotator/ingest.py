"""Video in, an annotatable clip out.

One pass does four things, in this order, because each depends on the last:

  1. decode the video and decide which source frames become annotation frames. This is
     where `extract_fps` resamples a 30 fps recording down to the rate the behaviour
     actually needs, and where `frame_max_width` downscales the image.
  2. write one JPEG per annotation frame. The browser needs frame accurate seeking, and
     an HTML5 video element does not give it reliably at low frame rates, so frames are
     served as images.
  3. run the detector over those frames, in the coordinates they were written in.
  4. associate the detections into tracks and assign each track to a zone.

Every coordinate in the project is therefore in stored-frame pixels. Zones in
project.yaml are read in the same space, which is the space the user sees in the browser,
so a zone can be read off a screenshot without arithmetic.

Re-running ingest on a clip that already exists is refused unless `overwrite` is set,
because it would silently invalidate labels already attached to those frames.
"""

import json
import os
import shutil
import time

from . import detect as det_mod
from . import track as track_mod
from .project import ProjectError


class IngestError(RuntimeError):
    pass


def _noop(*_a, **_k):
    pass


def clip_id_for(video_path, prefix=None):
    """A filesystem safe clip id from a video filename."""
    base = os.path.splitext(os.path.basename(video_path))[0]
    safe = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in base)
    safe = safe.lstrip("._") or "clip"
    return "%s__%s" % (prefix, safe) if prefix else safe


def probe(video_path):
    """(n_frames, w, h, fps) without decoding the whole file."""
    import cv2
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IngestError("cannot open video %s" % video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    cap.release()
    return n, w, h, round(float(fps), 4)


def _keep_stride(src_fps, extract_fps):
    """How many source frames to advance per annotation frame.

    A stride rather than timestamp seeking: seeking a variable frame rate file by time is
    unreliable across codecs, and a stride over a sequential decode is exact.
    """
    if not extract_fps or not src_fps or extract_fps >= src_fps:
        return 1
    return max(1, int(round(src_fps / float(extract_fps))))


def extract_frames(video_path, out_dir, extract_fps=None, frame_max_width=None,
                   window_frames=None, jpeg_quality=85, progress=_noop):
    """Decode, resample, downscale and write JPEGs. Returns the clip's frame metadata."""
    import cv2

    src_n, src_w, src_h, src_fps = probe(video_path)
    stride = _keep_stride(src_fps, extract_fps)
    scale = 1.0
    if frame_max_width and src_w and src_w > frame_max_width:
        scale = float(frame_max_width) / float(src_w)

    os.makedirs(out_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise IngestError("cannot open video %s" % video_path)

    kept, src_idx, w, h = 0, 0, 0, 0
    source_index = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if src_idx % stride == 0:
                if scale != 1.0:
                    frame = cv2.resize(frame, None, fx=scale, fy=scale,
                                       interpolation=cv2.INTER_AREA)
                if kept == 0:
                    h, w = frame.shape[:2]
                cv2.imwrite(os.path.join(out_dir, "%05d.jpg" % kept), frame,
                            [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
                source_index.append(src_idx)
                kept += 1
                if kept % 25 == 0:
                    progress("extracting frame %d" % kept)
                if window_frames and kept >= int(window_frames):
                    break
            src_idx += 1
    finally:
        cap.release()

    if kept == 0:
        raise IngestError("no frames decoded from %s" % video_path)

    fps = (src_fps / stride) if src_fps else float(extract_fps or 5.0)
    return {"n_frames": kept, "w": w, "h": h, "fps": round(float(fps), 4),
            "source": {"path": os.path.abspath(video_path), "n_frames": src_n,
                       "w": src_w, "h": src_h, "fps": src_fps, "stride": stride,
                       "scale": round(scale, 6), "frame_index": source_index}}


def _read_frames(frames_dir, n):
    import cv2
    out = []
    for i in range(n):
        img = cv2.imread(os.path.join(frames_dir, "%05d.jpg" % i))
        if img is None:
            raise IngestError("frame %d missing from %s" % (i, frames_dir))
        out.append(img)
    return out


def ingest_video(project, video_path, clip_id=None, detections=None, overwrite=False,
                 copy_video=True, progress=_noop, zones=None):
    """Add one video to a project. Returns the clip metadata that was written.

    `zones` overrides the project's zones for this clip alone, which is what a corpus
    filmed by several cameras needs: the enclosures sit at different pixels in each view,
    so one global set of rectangles cannot describe them all. The zones actually used are
    recorded in the clip's meta.json, so a clip always carries its own geometry.
    """
    video_path = os.path.abspath(os.path.expanduser(video_path))
    if not os.path.exists(video_path):
        raise IngestError("no video at %s" % video_path)
    clip_id = project.check_clip_id(clip_id or clip_id_for(video_path))

    if project.has_clip(clip_id) and not overwrite:
        raise IngestError(
            "clip %r already exists in this project. Pass --overwrite to rebuild it, "
            "which discards the frames and tracks but not the labels." % clip_id)

    cdir = project.clip_dir(clip_id)
    fdir = project.frames_dir(clip_id)
    if overwrite and os.path.isdir(fdir):
        shutil.rmtree(fdir)
    os.makedirs(cdir, exist_ok=True)

    stored_video = video_path
    if copy_video:
        os.makedirs(project.videos_dir, exist_ok=True)
        dest = os.path.join(project.videos_dir, os.path.basename(video_path))
        if os.path.abspath(dest) != video_path:
            if not os.path.exists(dest):
                progress("copying video into the project")
                shutil.copy2(video_path, dest)
            stored_video = dest

    cfg = project.config
    progress("decoding video")
    meta = extract_frames(
        stored_video, fdir,
        extract_fps=cfg.get("extract_fps"),
        frame_max_width=cfg.get("frame_max_width"),
        window_frames=cfg.get("window_frames"),
        jpeg_quality=cfg.get("jpeg_quality", 85),
        progress=progress)

    progress("running detection over %d frames" % meta["n_frames"])
    detector = det_mod.from_config(cfg.get("detector"), detections)
    frames = _read_frames(fdir, meta["n_frames"])
    dets = detector.run(frames, offset=0)
    del frames

    dcfg = cfg.get("detector") or {}
    dets = [det_mod.filter_boxes(d, dcfg.get("min_box_px", 0), dcfg.get("max_box_px"))
            for d in dets]
    n_det = sum(len(d) for d in dets)

    reused_ids = (isinstance(detector, det_mod.PrecomputedDetections)
                  and detector.has_tracks)
    if reused_ids:
        progress("reusing the track identities the detections file carried")
        ids = detector.track_ids(0, meta["n_frames"])
        tracks = track_mod.tracks_from_ids(dets, ids)
        tracker_desc = {"kind": "from_detections_file"}
    else:
        progress("associating %d detections into tracks" % n_det)
        tracker = track_mod.from_config(cfg.get("tracker"), meta["fps"])
        tracks = tracker.run(dets)
        tracker_desc = tracker.describe()

    zdef = zones if zones is not None else project.zones
    zone_of = {}
    if zdef:
        for tid, fb in tracks.items():
            zone_of[tid] = zdef.of_track(fb)
        n_out = sum(1 for z in zone_of.values() if z is None)
        if n_out:
            progress("%d of %d tracks fall outside every zone and will not be counted"
                     % (n_out, len(tracks)))
    else:
        for tid in tracks:
            zone_of[tid] = None

    project.save_tracks(clip_id, tracks, zone_of)

    meta.update({
        "clip": clip_id,
        "video": os.path.basename(stored_video),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_detections": n_det,
        "n_tracks": len(tracks),
        "detector": detector.describe(),
        "tracker": tracker_desc,
        "zones": zdef.as_list() if zdef else [],
        "states": [s["key"] for s in project.states.states],
    })
    tmp = project.clip_meta_path(clip_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(tmp, project.clip_meta_path(clip_id))
    progress("done: %d frames, %d detections, %d tracks"
             % (meta["n_frames"], n_det, len(tracks)))
    return meta


def ingest_many(project, video_paths, prefix=None, detections=None, overwrite=False,
                copy_video=True, progress=_noop, zones=None, clip_id=None):
    """Ingest several videos, reporting per video rather than failing the whole batch."""
    done, failed = [], []
    for i, v in enumerate(video_paths, 1):
        cid = clip_id if (clip_id and len(video_paths) == 1) else clip_id_for(v, prefix)
        try:
            progress("[%d/%d] %s" % (i, len(video_paths), cid))
            done.append(ingest_video(project, v, cid, detections, overwrite,
                                     copy_video, progress, zones))
        except (IngestError, ProjectError, det_mod.DetectorError, RuntimeError) as e:
            progress("[%d/%d] %s FAILED: %s" % (i, len(video_paths), cid, e))
            failed.append({"video": v, "clip": cid, "error": str(e)})
    return done, failed
