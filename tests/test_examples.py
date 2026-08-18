"""The bundled examples, ingested for real.

Skipped when the examples are not on disk, so a source checkout without the data still has
a green suite. This is the test that catches a manifest that no longer matches the files.
"""

import json
import os

import pytest

cv2 = pytest.importorskip("cv2")

from spatiotemporal_annotator import ingest as ingest_mod
from spatiotemporal_annotator import store
from spatiotemporal_annotator.project import Project
from spatiotemporal_annotator.zones import Zones

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(os.path.dirname(HERE), "examples")
MANIFEST = os.path.join(EX, "examples.json")

pytestmark = pytest.mark.skipif(not os.path.isfile(MANIFEST),
                                reason="bundled examples are not present")


@pytest.fixture(scope="module")
def manifest():
    with open(MANIFEST) as f:
        return json.load(f)


def test_the_manifest_matches_the_files_on_disk(manifest):
    assert manifest["clips"], "the manifest lists no clips"
    for ex in manifest["clips"]:
        assert os.path.isfile(os.path.join(EX, ex["video"])), ex["video"]
        assert os.path.isfile(os.path.join(EX, ex["detections"])), ex["detections"]


def test_every_example_declares_provenance_and_a_licence(manifest):
    src = manifest["source"]
    for key in ("study", "iacuc", "camera", "detector", "tracker", "license"):
        assert src.get(key), "examples.json source is missing %s" % key
    assert os.path.isfile(os.path.join(EX, "LICENSE-DATA.txt"))


def test_the_manifest_project_config_is_valid(manifest):
    # a broken states or zones block would only surface at `sta demo` time otherwise
    p = Project("/nonexistent", manifest["project"])
    assert p.states.rest == "r" and p.states.active == "a"
    for ex in manifest["clips"]:
        z = Zones(ex["zones"])
        assert len(z) == 2


def test_example_zones_differ_between_the_two_cameras(manifest):
    by_cam = {}
    for ex in manifest["clips"]:
        by_cam.setdefault(ex["tags"]["camera"], set()).update(
            z["name"] for z in ex["zones"])
    assert len(by_cam) >= 2
    names = list(by_cam.values())
    assert names[0] != names[1], "two cameras should see different enclosures"


def test_the_first_example_ingests_and_seeds_cleanly(tmp_path, manifest):
    ex = manifest["clips"][0]
    p = Project.create(str(tmp_path / "proj"), **manifest["project"])
    meta = ingest_mod.ingest_video(
        p, os.path.join(EX, ex["video"]), ex["clip"],
        os.path.join(EX, ex["detections"]), zones=Zones(ex["zones"]))

    assert meta["n_frames"] == manifest["project"]["window_frames"]
    assert meta["fps"] == pytest.approx(5.0)
    # the identities in the detections file are reused, so no association is run
    assert meta["tracker"]["kind"] == "from_detections_file"
    assert meta["n_tracks"] > 20

    frames, zones = p.load_tracks(ex["clip"])
    doc = store.seed_clip(p, ex["clip"], meta, frames, zones, "tester")
    rows = store.countable(doc)
    assert len(rows) == meta["n_tracks"]
    # the point of choosing these clips: every individual sits inside a zone
    assert all(r["zone"] for r in rows)
    assert store.require_zone(doc) is True
    # nothing is annotated yet, so census mode must refuse to close the clip
    assert store.mark_complete(doc, census_mode=True) is False


def test_the_example_detections_carry_track_ids(manifest):
    for ex in manifest["clips"]:
        with open(os.path.join(EX, ex["detections"])) as f:
            head = f.readline().strip()
        assert head.endswith("track_id"), ex["detections"]
