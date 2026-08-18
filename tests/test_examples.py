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


# ---- the bundled annotation ------------------------------------------------------
def test_every_example_ships_the_study_annotation(manifest):
    for ex in manifest["clips"]:
        assert ex.get("labels"), "%s has no labels entry" % ex["clip"]
        assert os.path.isfile(os.path.join(EX, ex["labels"])), ex["labels"]


def test_the_bundled_labels_are_complete_and_self_describing(manifest):
    for ex in manifest["clips"]:
        with open(os.path.join(EX, ex["labels"])) as f:
            doc = json.load(f)
        assert doc["clip"] == ex["clip"]
        assert doc["complete"] is True
        # each file carries its own state vocabulary, so a later ethogram change cannot
        # silently reinterpret work already done
        assert [s["key"] for s in doc["states"]] == ["r", "a"]
        assert doc["zones"] == sorted(z["name"] for z in ex["zones"])
        assert doc["individuals"]


def test_bundled_label_ids_are_one_scheme_with_no_dangling_merges(manifest):
    for ex in manifest["clips"]:
        with open(os.path.join(EX, ex["labels"])) as f:
            doc = json.load(f)
        ids = [r["individual_id"] for r in doc["individuals"]]
        assert len(set(ids)) == len(ids), "duplicate individual_id in %s" % ex["clip"]
        for i in ids:
            assert len(i) == 3 and i[0] == "i" and i[1:].isdigit(), i
        merged = {r["merged_into"] for r in doc["individuals"] if r.get("merged_into")}
        assert merged <= set(ids), "merged_into points outside the file in %s" % ex["clip"]


def test_bundled_labels_hold_the_one_box_one_individual_invariant(manifest):
    from spatiotemporal_annotator import core as cc
    for ex in manifest["clips"]:
        with open(os.path.join(EX, ex["labels"])) as f:
            doc = json.load(f)
        own = {}
        for r in doc["individuals"]:
            if r["status"] == "merged":
                continue
            assert len(r["fstate"]) == doc["n_frames"]
            for f_i, box in enumerate(r.get("boxes") or []):
                if box is None:
                    continue
                k = (cc.active_track(r["segments"], f_i), f_i)
                assert k not in own, "%s: %s owned twice" % (ex["clip"], k)
                own[k] = r["individual_id"]


def test_the_bundled_annotation_reproduces_the_study_counts(manifest):
    """The numbers the examples README publishes. If a re-conversion changes them,
    the README is wrong and this fails rather than the claim quietly drifting."""
    expected = {"cam1__20251014_082500": (18, 3700, 0),
                "cam3__20251021_115500": (18, 3099, 1),
                "cam3__20251103_091000": (65, 3198, 2),
                "cam1__20251110_040500": (20, 3000, 0)}
    for ex in manifest["clips"]:
        with open(os.path.join(EX, ex["labels"])) as f:
            doc = json.load(f)
        conf = [r for r in doc["individuals"] if r["status"] == "confirmed"]
        a = sum(r["fstate"].count("a") for r in conf)
        r_ = sum(r["fstate"].count("r") for r in conf)
        m = sum(r["fstate"].count("m") for r in conf)
        assert (a, a + r_, m) == expected[ex["clip"]], ex["clip"]


def test_demo_loads_the_annotation_and_blank_does_not(tmp_path, manifest):
    from spatiotemporal_annotator.cli import main
    root = str(tmp_path / "demo")
    assert main(["demo", root, "--no-serve"]) == 0
    p = Project.load(root)
    for ex in manifest["clips"]:
        doc = store.load(p, ex["clip"])
        assert doc is not None and doc["complete"] is True

    blank = str(tmp_path / "blank")
    assert main(["demo", blank, "--no-serve", "--blank"]) == 0
    pb = Project.load(blank)
    assert all(store.load(pb, ex["clip"]) is None for ex in manifest["clips"])


def test_demo_never_clobbers_annotation_already_in_the_project(tmp_path, manifest):
    from spatiotemporal_annotator.cli import main
    root = str(tmp_path / "demo")
    assert main(["demo", root, "--no-serve"]) == 0
    p = Project.load(root)
    cid = manifest["clips"][0]["clip"]
    doc = store.load(p, cid)
    doc["note"] = "my own work"
    store.save(p, doc)
    assert main(["demo", root, "--no-serve"]) == 0
    assert store.load(p, cid)["note"] == "my own work"
