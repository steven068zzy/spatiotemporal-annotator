"""Association and the detection backends that do not need a model on disk."""

import json
import os

import pytest

from spatiotemporal_annotator import detect as det
from spatiotemporal_annotator import track as tr


# ---- the simple tracker ---------------------------------------------------------
def _walk(n, x0=0, step=2, size=10):
    """One box drifting right, one detection a frame."""
    return [[[x0 + step * f, 0, x0 + step * f + size, size, 0.9, 0]] for f in range(n)]


def test_a_single_drifting_box_stays_one_track():
    out = tr.SimpleTracker(iou_match=0.3).run(_walk(10))
    assert len(out) == 1
    assert len(next(iter(out.values()))) == 10


def test_two_separated_boxes_stay_two_tracks():
    dets = [[[0, 0, 10, 10, .9, 0], [100, 0, 110, 10, .9, 0]] for _ in range(5)]
    out = tr.SimpleTracker().run(dets)
    assert len(out) == 2


def test_a_teleport_beyond_the_iou_threshold_starts_a_new_track():
    dets = [[[0, 0, 10, 10, .9, 0]], [[500, 0, 510, 10, .9, 0]]]
    out = tr.SimpleTracker(iou_match=0.3).run(dets)
    assert len(out) == 2


def test_a_gap_shorter_than_max_age_is_bridged():
    dets = [[[0, 0, 10, 10, .9, 0]], [], [[1, 0, 11, 10, .9, 0]]]
    out = tr.SimpleTracker(iou_match=0.3, max_age=5).run(dets)
    assert len(out) == 1
    # nothing is written for the empty frame: a coasting track holds no box, and 'm' is
    # the honest label for those frames
    assert sorted(next(iter(out.values()))) == [0, 2]


def test_a_gap_longer_than_max_age_splits_the_track():
    dets = [[[0, 0, 10, 10, .9, 0]], [], [], [], [[1, 0, 11, 10, .9, 0]]]
    out = tr.SimpleTracker(iou_match=0.3, max_age=1).run(dets)
    assert len(out) == 2


def test_min_hits_drops_a_one_frame_fragment():
    dets = [[[0, 0, 10, 10, .9, 0], [900, 0, 910, 10, .9, 0]],
            [[0, 0, 10, 10, .9, 0]], [[0, 0, 10, 10, .9, 0]]]
    assert len(tr.SimpleTracker(min_hits=1).run(dets)) == 2
    assert len(tr.SimpleTracker(min_hits=2).run(dets)) == 1


def test_an_empty_video_yields_no_tracks():
    assert tr.SimpleTracker().run([[], [], []]) == {}


def test_from_config_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        tr.from_config({"kind": "magic"}, 5.0)


def test_tracks_from_ids_uses_the_identities_in_the_file():
    dets = [[[0, 0, 10, 10, .9, 0], [50, 0, 60, 10, .9, 0]],
            [[1, 0, 11, 10, .9, 0]]]
    ids = [[7, 8], [7]]
    out = tr.tracks_from_ids(dets, ids)
    assert set(out) == {7, 8}
    assert sorted(out[7]) == [0, 1]


def test_tracks_from_ids_skips_a_detection_with_no_identity():
    out = tr.tracks_from_ids([[[0, 0, 10, 10, .9, 0], [50, 0, 60, 10, .9, 0]]],
                             [[7, None]])
    assert set(out) == {7}


# ---- detection backends ---------------------------------------------------------
def test_null_detector_explains_itself():
    with pytest.raises(det.DetectorError) as e:
        det.NullDetector().run([None])
    assert "detector.model" in str(e.value)


def test_precomputed_reads_a_csv_and_keeps_track_ids(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("frame,x1,y1,x2,y2,conf,track_id\n"
                 "0,1,2,3,4,0.9,11\n0,5,6,7,8,0.8,12\n1,1,2,3,4,0.7,11\n")
    d = det.PrecomputedDetections(str(p))
    assert d.has_tracks
    got = d.run([None, None])
    assert len(got[0]) == 2 and len(got[1]) == 1
    assert got[0][0][:4] == [1.0, 2.0, 3.0, 4.0]
    assert d.track_ids(0, 2) == [[11, 12], [11]]


def test_precomputed_without_track_ids_reports_so(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("frame,x1,y1,x2,y2\n0,1,2,3,4\n")
    d = det.PrecomputedDetections(str(p))
    assert not d.has_tracks
    assert d.run([None])[0][0][4] == 1.0        # conf defaults to 1.0


def test_precomputed_rejects_a_csv_missing_a_column(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("frame,x1,y1\n0,1,2\n")
    with pytest.raises(det.DetectorError) as e:
        det.PrecomputedDetections(str(p))
    assert "x2" in str(e.value)


def test_precomputed_reads_the_json_frames_shape(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"frames": {"0": [[1, 2, 3, 4, 0.9, 0]]}}))
    d = det.PrecomputedDetections(str(p))
    assert d.run([None])[0][0][:4] == [1.0, 2.0, 3.0, 4.0]


def test_precomputed_reads_a_flat_json_list(tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps([{"frame": 0, "x1": 1, "y1": 2, "x2": 3, "y2": 4}]))
    assert det.PrecomputedDetections(str(p)).run([None])[0][0][:4] == [1., 2., 3., 4.]


def test_precomputed_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(det.DetectorError):
        det.PrecomputedDetections(str(tmp_path / "nope.csv"))


def test_filter_boxes_drops_degenerate_and_out_of_range_boxes():
    dets = [[0, 0, 0, 10, .9, 0],       # zero width
            [0, 0, 4, 4, .9, 0],        # too small
            [0, 0, 50, 50, .9, 0],      # fine
            [0, 0, 500, 500, .9, 0]]    # too large
    got = det.filter_boxes(dets, min_side=8, max_side=200)
    assert got == [[0, 0, 50, 50, .9, 0]]


def test_from_config_prefers_an_explicit_detections_file(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("frame,x1,y1,x2,y2\n0,1,2,3,4\n")
    d = det.from_config({"model": "/does/not/exist.pt"}, str(p))
    assert isinstance(d, det.PrecomputedDetections)


def test_from_config_with_no_model_gives_the_null_backend():
    assert isinstance(det.from_config({}), det.NullDetector)


def test_from_config_reports_a_missing_checkpoint_rather_than_loading_it():
    with pytest.raises(det.DetectorError):
        det.from_config({"model": "/definitely/not/here.pt"})
