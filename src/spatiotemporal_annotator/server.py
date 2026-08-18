"""The annotation server. Binds to loopback only.

Every mutating endpoint takes the whole clip document, applies one change through
`core.py`, writes the file and returns the rows the browser should redraw. The browser
holds no authoritative state, so a reload always shows what is on disk, and a crash costs
at most the edit in flight.

Uploads take the file as the raw request body with the name in the query string rather
than as multipart form data. There is no multipart parser to get wrong, the write is a
straight chunked copy to disk, and a 100 MB checkpoint does not have to be held in memory.
"""

import json
import os
import posixpath
import re
import shutil
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import core as cc
from .export import export as export_labels
from . import ingest as ingest_mod
from . import stats as stats_mod
from . import store
from .project import Project, ProjectError

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
SAFE_NAME = re.compile(r"^[\w][\w\-. ]{0,120}$")
UPLOAD_LIMIT = 4 * 1024 * 1024 * 1024      # 4 GiB, a guard against a runaway body
CHUNK = 1024 * 1024

CTYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
          ".json": "application/json", ".jpg": "image/jpeg", ".png": "image/png"}


class Session:
    """Everything the handler needs. One per server, guarded by one lock."""

    def __init__(self, project, annotator=""):
        self.project = project
        self.annotator = annotator or project.config.get("annotator", "")
        self.lock = threading.RLock()
        self.jobs = {}          # job_id -> {"clip", "state", "log", "error"}
        self._job_n = 0

    @property
    def census_mode(self):
        return bool(self.project.config.get("census_mode"))

    def get_doc(self, clip_id, create=True):
        """The document for a clip, seeding and saving it the first time it is opened."""
        with self.lock:
            doc = store.load(self.project, clip_id)
            if doc is None and create:
                if not self.project.has_clip(clip_id):
                    return None
                meta = self.project.clip_meta(clip_id)
                frames, zones = self.project.load_tracks(clip_id)
                doc = store.seed_clip(self.project, clip_id, meta, frames, zones,
                                      self.annotator)
                store.save(self.project, doc)
            return doc

    def new_job(self, clip_id):
        with self.lock:
            self._job_n += 1
            jid = "job%d" % self._job_n
            self.jobs[jid] = {"clip": clip_id, "state": "running", "log": [],
                              "error": None}
            return jid

    def job_log(self, jid, msg):
        with self.lock:
            j = self.jobs.get(jid)
            if j is not None:
                j["log"].append(msg)
                del j["log"][:-40]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    session = None                      # injected by make_server

    def log_message(self, *a):
        pass

    # ---- plumbing ------------------------------------------------------------------
    def _send(self, body, ctype, code=200, extra=None):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj), "application/json", code)

    def _err(self, msg, code=400):
        self._json({"ok": False, "err": msg}, code)

    def _static(self, name):
        path = os.path.join(UI_DIR, posixpath.basename(name))
        if not os.path.isfile(path):
            return self.send_error(404)
        with open(path, "rb") as f:
            self._send(f.read(), CTYPES.get(os.path.splitext(path)[1],
                                            "application/octet-stream"))

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _query(self):
        parts = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(parts.query), parts.path

    # ---- GET -----------------------------------------------------------------------
    def do_GET(self):
        q, path = self._query()
        s = self.session
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path in ("/app.js", "/app.css"):
            return self._static(path.lstrip("/"))

        if path == "/api/config":
            cfg = s.project.ui_config()
            cfg["annotator"] = s.annotator
            cfg["root"] = s.project.root
            cfg["settings"] = self._editable_settings()
            return self._json(cfg)

        if path == "/api/clips":
            return self._json(stats_mod.clip_table(s.project))

        if path == "/api/stats":
            return self._json(stats_mod.aggregate(s.project))

        if path == "/api/jobs":
            with s.lock:
                return self._json(s.jobs)

        m = re.match(r"^/api/clip/([\w\-.]+)$", path)
        if m:
            doc = s.get_doc(m.group(1))
            if doc is None:
                return self._err("no such clip in this project", 404)
            return self._json({"doc": doc,
                               **store.clip_status(doc)})

        m = re.match(r"^/frame/([\w\-.]+)/(\d+)\.jpg$", path)
        if m:
            try:
                p = s.project.frame_path(m.group(1), int(m.group(2)))
            except ProjectError:
                return self.send_error(404)
            if not os.path.isfile(p):
                return self.send_error(404)
            with open(p, "rb") as f:
                return self._send(f.read(), "image/jpeg")

        self.send_error(404)

    # ---- POST ----------------------------------------------------------------------
    def do_POST(self):
        q, path = self._query()
        s = self.session

        if path == "/api/upload":
            return self._upload(q)
        if path == "/api/ingest":
            return self._ingest(self._body())
        if path == "/api/settings":
            return self._settings(self._body())
        if path == "/api/export":
            d = self._body()
            shapes = d.get("shapes") or ["frames", "bouts", "units"]
            rep = export_labels(s.project, shapes,
                                complete_only=bool(d.get("complete_only")))
            return self._json({"ok": True, "report": rep})

        d = self._body()
        clip = d.get("clip", "")
        with s.lock:
            if path == "/api/skip":
                store.skip(s.project, clip, s.annotator)
                return self._json({"ok": True})
            if path == "/api/unskip":
                store.unskip(s.project, clip)
                return self._json({"ok": True})

            doc = s.get_doc(clip, create=False)
            if doc is None:
                return self._err("clip not open", 400)
            states = s.project.states.as_dict()
            row = None
            if "individual_id" in d:
                row = next((b for b in doc["individuals"]
                            if b["individual_id"] == d["individual_id"]), None)
                if row is None:
                    return self._err("no such individual", 400)

            if path == "/api/paint":
                bad = set(d["fstate"]) - set(states["paintable"]) - {cc.NODATA}
                if bad:
                    return self._err("state character(s) %s are not in this project's "
                                     "vocabulary" % ", ".join(sorted(bad)))
                if len(d["fstate"]) != doc["n_frames"]:
                    return self._err("fstate must be exactly %d characters"
                                     % doc["n_frames"])
                row["fstate"] = d["fstate"]
                row["updated_at"] = _now()
                doc["individuals"] = cc.reconcile(
                    doc["individuals"], store.tracks_of(doc), doc["n_frames"],
                    doc["fps"], states=states)
                store.save(s.project, doc)
                return self._json({"ok": True, "individuals": doc["individuals"]})

            if path == "/api/confirm":
                if row["status"] in cc.DISMISSED:
                    # Confirming a merged row would un-merge it while leaving
                    # merged_into set, and the same animal would be counted twice.
                    # Un-merging is what re-anchoring is for; a discarded row comes
                    # back through /api/discard.
                    return self._json({"ok": False, "err": "row is %s" % row["status"],
                                       **store.clip_status(doc)})
                row["status"] = "confirmed" if d.get("confirmed", True) else "unseen"
                row["updated_at"] = _now()
                if row["status"] == "unseen":
                    doc["complete"] = False
                store.save(s.project, doc)
                return self._json({"ok": True,
                                   **store.clip_status(doc)})

            if path == "/api/discard":
                if row["status"] == "merged":
                    return self._json({"ok": False, "err": "row is merged",
                                       **store.clip_status(doc)})
                row["updated_at"] = _now()
                if d.get("discarded", True):
                    row["status"] = "discarded"
                elif row["status"] == "discarded":
                    row["status"] = "unseen"
                    doc["complete"] = False
                store.save(s.project, doc)
                return self._json({"ok": True,
                                   **store.clip_status(doc)})

            if path == "/api/note":
                if row is not None:
                    row["note"] = str(d.get("note", ""))[:2000]
                else:
                    doc["note"] = str(d.get("note", ""))[:4000]
                store.save(s.project, doc)
                return self._json({"ok": True})

            if path == "/api/reanchor":
                doc["individuals"] = cc.reanchor(
                    doc["individuals"], store.tracks_of(doc), doc["n_frames"],
                    doc["fps"], d["individual_id"], int(d["frame"]),
                    int(d["track_id"]), states=states)
                store.save(s.project, doc)
                return self._json({"ok": True, "individuals": doc["individuals"]})

            if path == "/api/complete":
                ok = store.mark_complete(doc, census_mode=s.census_mode)
                if ok:
                    store.save(s.project, doc)
                return self._json({"ok": ok,
                                   **store.clip_status(doc)})

        self.send_error(404)

    # ---- upload, ingest, settings --------------------------------------------------
    def _upload(self, q):
        """Raw body to a file. ?name=<basename>&kind=video|model|detections"""
        s = self.session
        name = (q.get("name") or [""])[0]
        kind = (q.get("kind") or ["video"])[0]
        name = urllib.parse.unquote(name)
        # Refused, not sanitised. Stripping a path component would silently store the file
        # under a different name than the one the caller asked for, and a caller sending a
        # path is a caller whose expectations are already wrong.
        if name != os.path.basename(name) or not SAFE_NAME.match(name):
            return self._err("refusing filename %r. Give a bare filename of letters, "
                             "digits, space, dot, dash or underscore." % name)
        if kind not in ("video", "model", "detections"):
            return self._err("kind must be video, model or detections")
        total = int(self.headers.get("Content-Length", 0) or 0)
        if total <= 0:
            return self._err("empty upload")
        if total > UPLOAD_LIMIT:
            return self._err("upload larger than the %d byte limit" % UPLOAD_LIMIT, 413)

        sub = {"video": "videos", "model": "models", "detections": "detections"}[kind]
        dest_dir = os.path.join(s.project.root, sub)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, name)
        tmp = dest + ".part"
        left = total
        try:
            with open(tmp, "wb") as f:
                while left > 0:
                    buf = self.rfile.read(min(CHUNK, left))
                    if not buf:
                        raise IOError("client closed the connection mid upload")
                    f.write(buf)
                    left -= len(buf)
            os.replace(tmp, dest)
        except (IOError, OSError) as e:
            if os.path.exists(tmp):
                os.remove(tmp)
            return self._err("upload failed: %s" % e, 500)

        if kind == "model":
            with s.lock:
                s.project.config.setdefault("detector", {})["model"] = dest
                s.project.save()
        return self._json({"ok": True, "path": dest, "bytes": total, "kind": kind})

    def _ingest(self, d):
        """Start a background ingest. Returns a job id the browser polls."""
        s = self.session
        video = d.get("video") or ""
        video = os.path.basename(str(video))
        path = os.path.join(s.project.videos_dir, video)
        if not os.path.isfile(path):
            return self._err("no video %r in the project's videos directory" % video)
        clip_id = d.get("clip") or ingest_mod.clip_id_for(path, d.get("prefix"))
        try:
            clip_id = s.project.check_clip_id(clip_id)
        except ProjectError as e:
            return self._err(str(e))
        if s.project.has_clip(clip_id) and not d.get("overwrite"):
            return self._err("clip %r already exists. Tick overwrite to rebuild it."
                             % clip_id)
        detections = d.get("detections") or None
        if detections:
            detections = os.path.join(s.project.root, "detections",
                                      os.path.basename(str(detections)))
        tags = {str(k): str(v) for k, v in (d.get("tags") or {}).items()}
        jid = s.new_job(clip_id)

        def work():
            try:
                meta = ingest_mod.ingest_video(
                    s.project, path, clip_id, detections,
                    overwrite=bool(d.get("overwrite")), copy_video=False,
                    progress=lambda m: s.job_log(jid, m))
                if tags:
                    meta["tags"] = tags
                    with open(s.project.clip_meta_path(clip_id), "w") as f:
                        json.dump(meta, f, indent=2)
                with s.lock:
                    s.jobs[jid]["state"] = "done"
            except Exception as e:                     # reported, never raised into a thread
                with s.lock:
                    s.jobs[jid]["state"] = "failed"
                    s.jobs[jid]["error"] = str(e)
                s.job_log(jid, "FAILED: %s" % e)

        threading.Thread(target=work, daemon=True).start()
        return self._json({"ok": True, "job": jid, "clip": clip_id})

    EDITABLE = {
        "extract_fps": (int, float, type(None)),
        "playback_fps": (int, float),
        "frame_max_width": (int, type(None)),
        "jpeg_quality": (int,),
        "window_frames": (int, type(None)),
        "display_max_scale": (int, float),
        "census_mode": (bool,),
        "annotator": (str,),
    }

    def _editable_settings(self):
        cfg = self.session.project.config
        out = {k: cfg.get(k) for k in self.EDITABLE}
        det = cfg.get("detector") or {}
        out["detector_model"] = det.get("model")
        out["detector_conf"] = det.get("conf")
        out["detector_imgsz"] = det.get("imgsz")
        out["tracker_kind"] = (cfg.get("tracker") or {}).get("kind")
        return out

    def _settings(self, d):
        """Change the settings a user legitimately tunes per corpus.

        Frame rate and frame size take effect on the NEXT ingest, never retroactively:
        rewriting the frames of a clip that already carries labels would move every box
        under every label already made.
        """
        s = self.session
        changed = {}
        with s.lock:
            for k, v in (d or {}).items():
                if k in self.EDITABLE:
                    if not isinstance(v, self.EDITABLE[k]):
                        return self._err("setting %s has the wrong type" % k)
                    s.project.config[k] = v
                    changed[k] = v
                elif k == "detector_model":
                    s.project.config.setdefault("detector", {})["model"] = v or None
                    changed[k] = v
                elif k == "detector_conf":
                    s.project.config.setdefault("detector", {})["conf"] = float(v)
                    changed[k] = v
                elif k == "detector_imgsz":
                    s.project.config.setdefault("detector", {})["imgsz"] = int(v)
                    changed[k] = v
                elif k == "tracker_kind":
                    if v not in ("simple", "bytetrack"):
                        return self._err("tracker_kind must be simple or bytetrack")
                    s.project.config.setdefault("tracker", {})["kind"] = v
                    changed[k] = v
                else:
                    return self._err("setting %r is not editable from the browser" % k)
            if "annotator" in changed:
                s.annotator = changed["annotator"]
            s.project.save()
        return self._json({"ok": True, "changed": changed,
                           "settings": self._editable_settings()})


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def make_server(project, annotator="", port=8767, host="127.0.0.1"):
    session = Session(project, annotator)
    handler = type("BoundHandler", (Handler,), {"session": session})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.session = session
    return srv


def serve(project_root, annotator="", port=8767, host="127.0.0.1", quiet=False):
    project = Project.load(project_root)
    srv = make_server(project, annotator, port, host)
    if not quiet:
        n = len(project.clip_ids())
        print("\n  project  %s" % project.config.get("name", ""))
        print("  root     %s" % project.root)
        print("  clips    %d" % n)
        print("  mode     %s" % ("census, every row must be resolved"
                                 if project.config.get("census_mode") else "free"))
        print("  states   %s" % ", ".join("%s = %s" % (s["key"], s["name"])
                                          for s in project.states.states))
        print("\n  >>> open  http://%s:%d  in your browser\n" % (host, port))
        if n == 0:
            print("  This project has no clips yet. Add one with\n"
                  "      sta add %s /path/to/video.mp4\n"
                  "  or drop a video onto the Add video panel in the browser.\n"
                  % project.root)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            print("\nstopped.")
    finally:
        srv.server_close()
    return srv


def free_space_ok(path, need_bytes):
    """True when `path` has room. Used before an ingest, which writes one JPEG a frame."""
    try:
        return shutil.disk_usage(path).free > need_bytes
    except OSError:
        return True
