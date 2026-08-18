"""The annotation logic. The browser mirrors these rules, so this is the copy that has to
be right, and a divergence between app.js and core.py is a bug in app.js."""

from spatiotemporal_annotator import core as cc


# ---- identity chain -------------------------------------------------------------
def test_active_track_walks_the_chain():
    segs = [{"from": 0, "track_id": 7}, {"from": 5, "track_id": 9},
            {"from": 8, "track_id": None}]
    got = [cc.active_track(segs, f) for f in range(10)]
    assert got == [7, 7, 7, 7, 7, 9, 9, 9, None, None]


def test_resolve_boxes_follows_the_chain_and_leaves_gaps():
    tf = {1: {0: [0, 0, 1, 1], 1: [1, 1, 2, 2]}, 2: {2: [2, 2, 3, 3]}}
    segs = [{"from": 0, "track_id": 1}, {"from": 2, "track_id": 2}]
    assert cc.resolve_boxes(segs, tf, 4) == [[0, 0, 1, 1], [1, 1, 2, 2],
                                             [2, 2, 3, 3], None]


def test_iou_of_disjoint_and_identical_boxes():
    assert cc.iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert cc.iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert cc.iou([0, 0, 0, 0], [0, 0, 0, 0]) == 0.0     # degenerate, not a crash


# ---- geometry rules -------------------------------------------------------------
def test_apply_geometry_marks_missing_boxes_inside_the_span():
    boxes = [[0, 0, 1, 1], None, [0, 0, 1, 1]]
    assert cc.apply_geometry("", boxes) == "rmr"


def test_apply_geometry_blanks_outside_the_span_but_keeps_a_painted_missed():
    boxes = [None, [0, 0, 1, 1], None]
    assert cc.apply_geometry("aam", boxes) == "-am"      # trailing 'm' is a human claim
    assert cc.apply_geometry("aaa", boxes) == "-a-"      # a painted state is refused there


def test_apply_geometry_promotes_a_stale_nodata_back_to_baseline():
    # after a re-anchor gave this frame a box, the old '-' must not survive
    assert cc.apply_geometry("-", [[0, 0, 1, 1]]) == "r"


def test_apply_geometry_pads_and_truncates_to_the_box_count():
    assert cc.apply_geometry("aaaaa", [[0, 0, 1, 1]] * 2) == "aa"
    assert cc.apply_geometry("a", [[0, 0, 1, 1]] * 3) == "arr"


# ---- fractions and bouts --------------------------------------------------------
def test_active_frac_ignores_missed_and_nodata():
    assert cc.active_frac("aarr") == 0.5
    assert cc.active_frac("aarrmm--") == 0.5
    assert cc.active_frac("----") == 0.0


def test_bouts_are_inclusive_and_carry_seconds():
    got = cc.bouts("rraarr", 5.0)
    assert got == [{"start": 2, "end": 3, "t0": 0.4, "t1": 0.6}]


def test_a_bout_running_to_the_last_frame_is_closed():
    assert cc.bouts("rraa", 5.0)[0]["end"] == 3


def test_state_fracs_covers_every_observable_state():
    assert cc.state_fracs("aarr") == {"r": 0.5, "a": 0.5}


# ---- painting -------------------------------------------------------------------
def test_paint_range_writes_a_half_open_interval():
    assert cc.paint_range("rrrr", 1, 3, "a") == "raar"


def test_paint_range_stops_at_a_nodata_gap():
    assert cc.paint_range("rr-rr", 0, 5, "a") == "aa-rr"


def test_paint_range_backward_stops_on_the_other_side():
    assert cc.paint_range("rr-rr", 0, 5, "a", backward=True) == "rr-aa"


def test_paint_range_ignores_an_empty_or_inverted_range():
    assert cc.paint_range("rrrr", 2, 2, "a") == "rrrr"
    assert cc.paint_range("rrrr", 3, 1, "a") == "rrrr"


def test_boundaries_skips_edges_touching_nodata():
    assert cc.boundaries("rraa") == [2]
    assert cc.boundaries("rr-aa") == []          # neither edge of the gap is draggable


# ---- countable and overlay ------------------------------------------------------
def test_countable_needs_a_zone_only_when_zones_exist():
    row = {"status": "unseen", "zone": None}
    assert cc.countable(row) is True
    assert cc.countable(row, require_zone=True) is False
    assert cc.countable({"status": "unseen", "zone": "A"}, require_zone=True) is True


def test_countable_refuses_a_dismissed_row_either_way():
    for status in ("merged", "discarded"):
        assert cc.countable({"status": status, "zone": "A"}) is False


def test_overlay_lists_every_distinct_rectangle_countable_first():
    rows = [
        {"individual_id": "i00", "status": "unseen", "zone": "A",
         "boxes": [[0, 0, 1, 1]]},
        {"individual_id": "i01", "status": "unseen", "zone": None,
         "boxes": [[5, 5, 6, 6]]},
    ]
    got = cc.overlay(rows, 0, require_zone=True)
    assert [e["individual_id"] for e in got] == ["i00", "i01"]
    assert [e["kind"] for e in got] == ["individual", "orphan"]


def test_overlay_drops_a_rectangle_a_countable_row_already_claimed():
    same = [[0, 0, 1, 1]]
    rows = [{"individual_id": "i00", "status": "confirmed", "zone": "A", "boxes": same},
            {"individual_id": "i01", "status": "merged", "zone": "A", "boxes": same}]
    got = cc.overlay(rows, 0, require_zone=True)
    assert [e["individual_id"] for e in got] == ["i00"]


# ---- reconcile ------------------------------------------------------------------
def _rows(*specs):
    return [cc.new_row(i, tid, zone) for i, tid, zone in specs]


def test_reconcile_merges_a_row_left_with_no_boxes():
    tf = {1: {0: [0, 0, 1, 1]}}
    rows = _rows(("i00", 1, "A"), ("i01", 99, "A"))     # track 99 does not exist
    out = cc.reconcile(rows, tf, 1, 5.0, taker="i00")
    by = {r["individual_id"]: r for r in out}
    assert by["i01"]["status"] == "merged"
    assert by["i01"]["merged_into"] == "i00"
    assert by["i00"]["status"] == "unseen"


def test_reconcile_adopts_an_unowned_box_into_an_auto_split_row():
    tf = {1: {0: [0, 0, 1, 1]}, 2: {0: [5, 5, 6, 6]}}
    out = cc.reconcile(_rows(("i00", 1, "A")), tf, 1, 5.0)
    extra = [r for r in out if r["source"] == "auto_split"]
    assert len(extra) == 1
    assert extra[0]["seed_track_id"] == 2
    assert extra[0]["zone"] is None                # no zone, so it is not counted as work


def test_reconcile_splits_one_run_per_contiguous_stretch():
    tf = {1: {0: [0, 0, 1, 1]}, 2: {0: [5, 5, 6, 6], 1: [5, 5, 6, 6],
                                    3: [5, 5, 6, 6]}}
    out = cc.reconcile(_rows(("i00", 1, "A")), tf, 4, 5.0)
    extra = [r for r in out if r["source"] == "auto_split"]
    assert len(extra) == 2                         # frames 0-1 and frame 3


def test_reconcile_never_overrides_a_human_discard():
    tf = {1: {0: [0, 0, 1, 1]}}
    rows = _rows(("i00", 1, "A"))
    rows[0]["status"] = "discarded"
    out = cc.reconcile(rows, tf, 1, 5.0)
    assert out[0]["status"] == "discarded"


def test_reconcile_refreshes_derived_fields():
    tf = {1: {0: [0, 0, 1, 1], 1: [0, 0, 1, 1]}}
    rows = _rows(("i00", 1, "A"))
    rows[0]["fstate"] = "aa"
    out = cc.reconcile(rows, tf, 2, 5.0)
    assert out[0]["n_present"] == 2
    assert out[0]["active_frac"] == 1.0
    assert out[0]["bouts"] == [{"start": 0, "end": 1, "t0": 0.0, "t1": 0.2}]


# ---- reanchor -------------------------------------------------------------------
def test_reanchor_hands_a_track_over_and_truncates_the_previous_owner():
    tf = {1: {0: [0, 0, 1, 1], 1: [0, 0, 1, 1]},
          2: {0: [5, 5, 6, 6], 1: [5, 5, 6, 6]}}
    rows = _rows(("i00", 1, "A"), ("i01", 2, "A"))
    out = cc.reanchor(rows, tf, 2, 5.0, "i00", 1, 2)
    by = {r["individual_id"]: r for r in out}
    assert by["i00"]["boxes"] == [[0, 0, 1, 1], [5, 5, 6, 6]]
    assert by["i01"]["boxes"][1] is None           # truncated at the re-anchor frame


def test_reanchor_keeps_the_orphaned_tail_as_a_split_row():
    tf = {1: {0: [0, 0, 1, 1], 1: [0, 0, 1, 1]},
          2: {0: [5, 5, 6, 6], 1: [5, 5, 6, 6]}}
    rows = _rows(("i00", 1, "A"), ("i01", 2, "A"))
    out = cc.reanchor(rows, tf, 2, 5.0, "i00", 1, 2)
    # i00's own tail on track 1 at frame 1 is no longer owned, so it must resurface
    owners = [r for r in out
              if any(b == [0, 0, 1, 1] for b in (r["boxes"] or [])[1:2])]
    assert owners, "the orphaned tail was silently deleted"


def test_reanchor_preserves_the_painted_state_where_a_box_survives():
    tf = {1: {0: [0, 0, 1, 1], 1: [0, 0, 1, 1]},
          2: {0: [5, 5, 6, 6], 1: [5, 5, 6, 6]}}
    rows = _rows(("i00", 1, "A"), ("i01", 2, "A"))
    rows[0]["fstate"] = "aa"
    out = cc.reanchor(rows, tf, 2, 5.0, "i00", 1, 2)
    by = {r["individual_id"]: r for r in out}
    assert by["i00"]["fstate"] == "aa"
