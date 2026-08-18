"""One synthetic video through the whole pipeline, then the HTTP surface against it.

The video is generated here rather than shipped, so this suite needs no example data and
runs in a couple of seconds. The bundled examples are exercised by
test_examples.py, which skips itself when they are absent.
"""

import json
import os
import threading
import urllib.error
import urllib.request

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from spatiotemporal_annotator import export as export_mod
from spatiotemporal_annotator import ingest as ingest_mod
from spatiotemporal_annotator import server as server_mod
from spatiotemporal_annotator import stats as stats_mod
from spatiotemporal_annotator import store
from spatiotemporal_annotator.project import Project, ProjectError

W, H, N, FPS = 320, 160, 20, 10.0


def _write_video(path, n=N, fps=FPS):
    """Two squares, one drifting right in the left half, one still in the right half."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(path, fourcc, fps, (W, H))
    assert vw.isOpened(), "OpenCV cannot write mp4v here"
    for f in range(n):
        img = np.full((H, W, 3), 30, np.uint8)
        x = 10 + 4 * f
        cv2.rectangle(img, (x, 60), (x + 20, 80), (200, 200, 200), -1)
        cv2.rectangle(img, (240, 60), (260, 80), (120, 200, 120), -1)
        vw.write(img)
    vw.release()
    return path


def _write_dets(path, n=N, with_ids=True):
    head = "frame,x1,y1,x2,y2,conf,track_id\n" if with_ids else "frame,x1,y1,x2,y2\n"
    lines = [head]
    for f in range(n):
        x = 10 + 4 * f
        if with_ids:
            lines.append("%d,%d,60,%d,80,0.9,1\n" % (f, x, x + 20))
            lines.append("%d,240,60,260,80,0.9,2\n" % f)
        else:
            lines.append("%d,%d,60,%d,80\n" % (f, x, x + 20))
            lines.append("%d,240,60,260,80\n" % f)
    with open(path, "w") as fh:
        fh.writelines(lines)
    return path


@pytest.fixture
def built(tmp_path):
    """A project with one ingested clip, two zones, two individuals."""
    root = tmp_path / "proj"
    p = Project.create(str(root), name="test", extract_fps=None, playback_fps=10,
                       zones=[{"name": "left", "rect": [0, 0, 160, H]},
                              {"name": "right", "rect": [160, 0, W, H]}])
    vid = _write_video(str(tmp_path / "clip.mp4"))
    dets = _write_dets(str(tmp_path / "d.csv"))
    meta = ingest_mod.ingest_video(p, vid, "clip", dets)
    return p, meta


# ---- ingest ---------------------------------------------------------------------
def test_ingest_writes_frames_tracks_and_meta(built):
    p, meta = built
    assert meta["n_frames"] == N
    assert (meta["w"], meta["h"]) == (W, H)
    assert meta["n_tracks"] == 2
    assert os.path.isfile(p.frame_path("clip", 0))
    assert os.path.isfile(p.frame_path("clip", N - 1))
    frames, zones = p.load_tracks("clip")
    assert set(frames) == {1, 2}
    assert set(zones.values()) == {"left", "right"}


def test_ingest_reuses_track_ids_from_the_detections_file(built):
    _, meta = built
    assert meta["tracker"]["kind"] == "from_detections_file"


def test_ingest_runs_the_tracker_when_the_file_has_no_ids(tmp_path):
    p = Project.create(str(tmp_path / "p"))
    vid = _write_video(str(tmp_path / "c.mp4"))
    dets = _write_dets(str(tmp_path / "d.csv"), with_ids=False)
    meta = ingest_mod.ingest_video(p, vid, "clip", dets)
    assert meta["tracker"]["kind"] == "simple"
    assert meta["n_tracks"] == 2


def test_extract_fps_resamples_and_records_the_stride(tmp_path):
    # a 10 fps source annotated at 5 fps must halve the frames and halve the clip's fps
    p = Project.create(str(tmp_path / "p"), extract_fps=5)
    vid = _write_video(str(tmp_path / "c.mp4"), n=20, fps=10.0)
    meta = ingest_mod.ingest_video(p, vid, "clip",
                                   _write_dets(str(tmp_path / "d.csv")))
    assert meta["n_frames"] == 10
    assert meta["fps"] == pytest.approx(5.0)
    assert meta["source"]["stride"] == 2


def test_frame_max_width_downscales_and_records_the_factor(tmp_path):
    p = Project.create(str(tmp_path / "p"), frame_max_width=160)
    vid = _write_video(str(tmp_path / "c.mp4"))
    meta = ingest_mod.ingest_video(p, vid, "clip",
                                   _write_dets(str(tmp_path / "d.csv")))
    assert meta["w"] == 160
    assert meta["source"]["scale"] == pytest.approx(0.5)
    img = cv2.imread(p.frame_path("clip", 0))
    assert img.shape[1] == 160


def test_window_frames_caps_the_annotated_length(tmp_path):
    p = Project.create(str(tmp_path / "p"), window_frames=7)
    vid = _write_video(str(tmp_path / "c.mp4"))
    meta = ingest_mod.ingest_video(p, vid, "clip",
                                   _write_dets(str(tmp_path / "d.csv")))
    assert meta["n_frames"] == 7


def test_re_ingesting_without_overwrite_is_refused(built, tmp_path):
    p, _ = built
    with pytest.raises(ingest_mod.IngestError) as e:
        ingest_mod.ingest_video(p, str(tmp_path / "clip.mp4"), "clip",
                                str(tmp_path / "d.csv"))
    assert "overwrite" in str(e.value)


def test_zones_can_be_overridden_per_clip(tmp_path):
    from spatiotemporal_annotator.zones import Zones
    p = Project.create(str(tmp_path / "p"),
                       zones=[{"name": "whole", "rect": [0, 0, W, H]}])
    vid = _write_video(str(tmp_path / "c.mp4"))
    meta = ingest_mod.ingest_video(
        p, vid, "clip", _write_dets(str(tmp_path / "d.csv")),
        zones=Zones([{"name": "otherA", "rect": [0, 0, 160, H]},
                     {"name": "otherB", "rect": [160, 0, W, H]}]))
    assert [z["name"] for z in meta["zones"]] == ["otherA", "otherB"]
    _, zones = p.load_tracks("clip")
    assert set(zones.values()) == {"otherA", "otherB"}


def test_a_clip_id_that_is_a_path_is_refused(built, tmp_path):
    p, _ = built
    with pytest.raises(ProjectError):
        ingest_mod.ingest_video(p, str(tmp_path / "clip.mp4"), "../escape",
                                str(tmp_path / "d.csv"))


# ---- project and store ----------------------------------------------------------
def test_a_project_round_trips_through_disk(built):
    p, _ = built
    again = Project.load(p.root)
    assert again.config["name"] == "test"
    assert again.zones.names == ["left", "right"]
    assert again.clip_ids() == ["clip"]


def test_loading_a_missing_project_says_how_to_make_one(tmp_path):
    with pytest.raises(ProjectError) as e:
        Project.load(str(tmp_path / "nope"))
    assert "sta init" in str(e.value)


def test_seed_creates_one_row_per_track_with_its_zone(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones, "tester")
    assert len(doc["individuals"]) == 2
    assert sorted(r["zone"] for r in doc["individuals"]) == ["left", "right"]
    assert all(r["status"] == "unseen" for r in doc["individuals"])
    assert all(len(r["fstate"]) == N for r in doc["individuals"])


def test_census_mode_blocks_completion_until_every_row_is_confirmed(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones)
    assert store.mark_complete(doc, census_mode=True) is False
    for r in doc["individuals"]:
        r["status"] = "confirmed"
    assert store.mark_complete(doc, census_mode=True) is True


def test_free_mode_lets_the_annotator_decide(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones)
    assert store.mark_complete(doc, census_mode=False) is True


# ---- HTTP -----------------------------------------------------------------------
class Client:
    def __init__(self, base):
        self.base = base

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return json.loads(r.read())

    def post(self, path, body=None, raw=None):
        data = raw if raw is not None else json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        if raw is None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def status(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code


@pytest.fixture
def client(built):
    p, _ = built
    srv = server_mod.make_server(p, annotator="tester", port=0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield Client("http://127.0.0.1:%d" % srv.server_address[1]), p
    srv.shutdown()
    srv.server_close()


def test_config_describes_the_project_to_the_browser(client):
    c, _ = client
    cfg = c.get("/api/config")
    assert cfg["zones"] == ["left", "right"]
    assert cfg["states"]["rest"] == "r"
    assert cfg["settings"]["playback_fps"] == 10


def test_index_and_assets_are_served(client):
    c, _ = client
    assert c.status("/") == 200
    assert c.status("/app.js") == 200
    assert c.status("/app.css") == 200


def test_opening_a_clip_seeds_and_persists_it(client):
    c, p = client
    r = c.get("/api/clip/clip")
    assert r["total"] == 2 and r["done"] == 0
    assert os.path.isfile(p.label_path("clip"))


def test_an_unknown_clip_is_a_404_with_a_reason(client):
    c, _ = client
    assert c.status("/api/clip/nosuch") == 404


def test_frames_are_served_and_a_missing_one_is_404(client):
    c, _ = client
    assert c.status("/frame/clip/00000.jpg") == 200
    assert c.status("/frame/clip/09999.jpg") == 404


def test_paint_confirm_and_complete_walk_the_whole_loop(client):
    c, p = client
    doc = c.get("/api/clip/clip")["doc"]
    ids = [r["individual_id"] for r in doc["individuals"]]
    fs = "a" * 5 + "r" * (N - 5)
    r = c.post("/api/paint", {"clip": "clip", "individual_id": ids[0], "fstate": fs})
    assert r["ok"]
    for i in ids:
        assert c.post("/api/confirm", {"clip": "clip", "individual_id": i})["ok"]
    assert c.post("/api/complete", {"clip": "clip"})["ok"]
    saved = store.load(p, "clip")
    assert saved["complete"] is True
    painted = next(r for r in saved["individuals"] if r["individual_id"] == ids[0])
    assert painted["fstate"] == fs
    assert painted["active_frac"] == pytest.approx(5 / N)
    assert painted["bouts"][0]["end"] == 4


def test_paint_refuses_a_character_outside_the_vocabulary(client):
    c, _ = client
    ids = [r["individual_id"] for r in c.get("/api/clip/clip")["doc"]["individuals"]]
    r = c.post("/api/paint", {"clip": "clip", "individual_id": ids[0],
                              "fstate": "q" * N})
    assert not r["ok"] and "vocabulary" in r["err"]


def test_paint_refuses_a_wrong_length_state_string(client):
    c, _ = client
    ids = [r["individual_id"] for r in c.get("/api/clip/clip")["doc"]["individuals"]]
    r = c.post("/api/paint", {"clip": "clip", "individual_id": ids[0], "fstate": "ar"})
    assert not r["ok"] and "characters" in r["err"]


def test_discard_takes_a_row_out_of_the_count_and_restores_it(client):
    c, _ = client
    ids = [r["individual_id"] for r in c.get("/api/clip/clip")["doc"]["individuals"]]
    r = c.post("/api/discard", {"clip": "clip", "individual_id": ids[0]})
    assert r["total"] == 1
    r = c.post("/api/discard", {"clip": "clip", "individual_id": ids[0],
                                "discarded": False})
    assert r["total"] == 2


def test_unconfirming_a_row_reopens_a_completed_clip(client):
    c, _ = client
    ids = [r["individual_id"] for r in c.get("/api/clip/clip")["doc"]["individuals"]]
    for i in ids:
        c.post("/api/confirm", {"clip": "clip", "individual_id": i})
    c.post("/api/complete", {"clip": "clip"})
    r = c.post("/api/confirm", {"clip": "clip", "individual_id": ids[0],
                                "confirmed": False})
    assert r["complete"] is False


def test_reanchor_moves_a_track_and_keeps_every_box_owned(client):
    c, _ = client
    doc = c.get("/api/clip/clip")["doc"]
    rows = {r["individual_id"]: r for r in doc["individuals"]}
    a, b = list(rows)
    tid_b = rows[b]["seed_track_id"]
    r = c.post("/api/reanchor", {"clip": "clip", "individual_id": a, "frame": 10,
                                 "track_id": tid_b})
    assert r["ok"]
    got = r["individuals"]
    taker = next(x for x in got if x["individual_id"] == a)
    assert taker["boxes"][10] == rows[b]["boxes"][10]
    # every box that existed before is still owned by somebody
    before = {tuple(bx) for x in doc["individuals"] for bx in x["boxes"] if bx}
    after = {tuple(bx) for x in got for bx in x["boxes"] if bx}
    assert before == after


def test_settings_change_the_project_file_and_come_back(client):
    c, p = client
    r = c.post("/api/settings", {"extract_fps": 5, "frame_max_width": 640,
                                 "census_mode": True, "annotator": "someone"})
    assert r["ok"]
    again = Project.load(p.root)
    assert again.config["extract_fps"] == 5
    assert again.config["frame_max_width"] == 640
    assert again.config["census_mode"] is True
    assert c.get("/api/config")["settings"]["annotator"] == "someone"


def test_settings_refuses_an_unknown_key_and_a_bad_tracker(client):
    c, _ = client
    assert not c.post("/api/settings", {"root": "/etc"})["ok"]
    assert not c.post("/api/settings", {"tracker_kind": "magic"})["ok"]


def test_upload_writes_the_file_and_refuses_a_path(client):
    c, p = client
    r = c.post("/api/upload?kind=video&name=up.mp4", raw=b"not really a video")
    assert r["ok"]
    assert os.path.isfile(os.path.join(p.videos_dir, "up.mp4"))
    bad = c.post("/api/upload?kind=video&name=../escape.mp4", raw=b"x")
    assert not bad["ok"]


def test_upload_of_a_model_points_the_project_at_it(client):
    c, p = client
    r = c.post("/api/upload?kind=model&name=w.pt", raw=b"stub")
    assert r["ok"]
    assert Project.load(p.root).config["detector"]["model"] == r["path"]


def test_skip_hides_a_clip_and_unskip_brings_it_back(client):
    c, p = client
    c.post("/api/skip", {"clip": "clip"})
    assert p.is_skipped("clip")
    assert c.get("/api/clips")[0]["skipped"] is True
    c.post("/api/unskip", {"clip": "clip"})
    assert not p.is_skipped("clip")


def test_stats_counts_what_is_on_disk(client):
    c, _ = client
    ids = [r["individual_id"] for r in c.get("/api/clip/clip")["doc"]["individuals"]]
    c.post("/api/paint", {"clip": "clip", "individual_id": ids[0],
                          "fstate": "a" * 4 + "r" * (N - 4)})
    for i in ids:
        c.post("/api/confirm", {"clip": "clip", "individual_id": i})
    c.post("/api/complete", {"clip": "clip"})
    s = c.get("/api/stats")
    assert s["clips"]["complete"] == 1
    assert s["individuals_confirmed"] == 2
    assert s["frames"]["individual"] == 2 * N
    assert s["primary_fraction"] == pytest.approx(4 / (2 * N))


def test_export_over_http_writes_three_shapes(client):
    c, p = client
    c.get("/api/clip/clip")
    r = c.post("/api/export", {})
    assert r["ok"]
    for shape in ("frames", "bouts", "units"):
        assert os.path.isfile(r["report"][shape]["path"])


# ---- export ---------------------------------------------------------------------
def test_export_shapes_have_the_expected_row_counts(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones, "tester")
    doc["individuals"][0]["fstate"] = "a" * 5 + "r" * (N - 5)
    doc["individuals"][0]["status"] = "confirmed"
    doc["individuals"][1]["status"] = "confirmed"
    doc["complete"] = True
    store.save(p, doc)
    rep = export_mod.export(p)
    assert rep["frames"]["rows"] == 2 * N
    assert rep["units"]["rows"] == 2            # one per zone
    with open(rep["units"]["path"]) as fh:
        body = fh.read()
    assert "frac_a" in body and "frac_r" in body
    with open(rep["bouts"]["path"]) as fh:
        bouts = fh.read().strip().splitlines()
    assert len(bouts) == 2                      # header plus one active bout


def test_export_complete_only_skips_an_unfinished_clip(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones)
    store.save(p, doc)
    rep = export_mod.export(p, ["frames"], complete_only=True)
    assert rep["frames"]["rows"] == 0


def test_export_excludes_a_discarded_row_but_counts_it(built):
    p, meta = built
    frames, zones = p.load_tracks("clip")
    doc = store.seed_clip(p, "clip", meta, frames, zones)
    doc["individuals"][0]["status"] = "discarded"
    store.save(p, doc)
    rep = export_mod.export(p, ["frames"])
    assert rep["frames"]["rows"] == N
    assert rep["discarded_rows"] == 1


def test_status_text_mentions_the_clip_and_the_mode(built):
    p, _ = built
    text = stats_mod.format_status(p)
    assert "clip" in text and "free" in text
