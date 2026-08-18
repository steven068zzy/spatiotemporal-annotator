"""The two things a project configures by hand, so a bad configuration must fail loudly
at load time rather than silently mislabel a corpus."""

import pytest

from spatiotemporal_annotator.states import States, StateConfigError
from spatiotemporal_annotator.zones import Zones, ZoneConfigError


# ---- states ---------------------------------------------------------------------
def test_default_states_are_resting_and_active():
    s = States()
    assert s.rest == "r" and s.active == "a"
    assert s.paintable == ("r", "a", "m")


def test_a_custom_ethogram_keeps_its_order_and_roles():
    s = States([{"key": "l", "name": "lying"},
                {"key": "w", "name": "walking"},
                {"key": "f", "name": "feeding"}])
    assert s.rest == "l"           # first state becomes the baseline
    assert s.active == "w"         # first painted state becomes primary
    assert [x["name"] for x in s.states] == ["lying", "walking", "feeding"]


def test_primary_can_be_declared_out_of_order():
    s = States([{"key": "l", "name": "lying", "baseline": True},
                {"key": "w", "name": "walking"},
                {"key": "f", "name": "feeding", "primary": True}])
    assert s.active == "f"


def test_every_state_gets_a_distinct_colour():
    s = States([{"key": "l", "name": "lying"}, {"key": "w", "name": "walking"},
                {"key": "f", "name": "feeding"}])
    cols = [x["color"] for x in s.states]
    assert len(set(cols)) == len(cols)


@pytest.mark.parametrize("spec,why", [
    ([{"key": "m", "name": "moving"}], "'m' is reserved for a missed detection"),
    ([{"key": "-", "name": "gap"}], "'-' is reserved for no data"),
    ([{"key": "c", "name": "climbing"}], "'c' is the confirm key"),
    ([{"key": "x", "name": "xxx"}], "'x' is the discard key"),
    ([{"key": "ab", "name": "two chars"}], "a key is one character"),
    ([{"key": "a", "name": "one"}, {"key": "a", "name": "two"}], "duplicate key"),
    ([{"key": "a"}], "a state needs a name"),
    ([{"key": "a", "name": "only one", "baseline": True}], "nothing left to annotate"),
    ([{"key": "a", "name": "a", "baseline": True},
      {"key": "b", "name": "b", "baseline": True}], "two baselines"),
])
def test_bad_state_specs_are_refused(spec, why):
    with pytest.raises(StateConfigError):
        States(spec)


def test_the_baseline_cannot_also_be_primary():
    with pytest.raises(StateConfigError):
        States([{"key": "r", "name": "rest", "baseline": True, "primary": True},
                {"key": "a", "name": "active"}])


def test_as_dict_carries_what_the_browser_needs():
    d = States().as_dict()
    assert d["rest"] == "r" and d["active"] == "a"
    assert d["missed"] == "m" and d["nodata"] == "-"
    assert "r" in d["observable"] and "m" not in d["observable"]


# ---- zones ----------------------------------------------------------------------
def test_no_zones_is_falsey_and_assigns_nothing():
    z = Zones()
    assert not z
    assert z.names == []
    assert z.of_box([0, 0, 10, 10]) is None


def test_a_rect_zone_holds_a_box_whose_centre_is_inside():
    z = Zones([{"name": "left", "rect": [0, 0, 100, 100]},
               {"name": "right", "rect": [100, 0, 200, 100]}])
    assert z.of_box([10, 10, 30, 30]) == "left"
    assert z.of_box([150, 10, 170, 30]) == "right"
    assert z.of_box([500, 500, 510, 510]) is None


def test_a_polygon_zone_works_the_same_way():
    z = Zones([{"name": "tri", "polygon": [[0, 0], [100, 0], [0, 100]]}])
    assert z.of_box([5, 5, 15, 15]) == "tri"
    assert z.of_box([90, 90, 99, 99]) is None


def test_a_track_takes_the_zone_of_its_first_box():
    # first box rather than a majority vote: an individual that walks out of frame keeps
    # the zone it was annotated in, so a row never moves between grid columns mid task
    z = Zones([{"name": "left", "rect": [0, 0, 100, 100]},
               {"name": "right", "rect": [100, 0, 200, 100]}])
    track = {5: [150, 10, 170, 30], 2: [10, 10, 30, 30], 9: [150, 10, 170, 30]}
    assert z.of_track(track) == "left"
    assert z.of_track({}) is None


def test_overlapping_zones_are_resolved_by_config_order():
    z = Zones([{"name": "first", "rect": [0, 0, 100, 100]},
               {"name": "second", "rect": [0, 0, 100, 100]}])
    assert z.of_box([10, 10, 20, 20]) == "first"


def test_scaled_rewrites_both_shapes():
    z = Zones([{"name": "r", "rect": [0, 0, 100, 50]},
               {"name": "p", "polygon": [[0, 0], [10, 0], [0, 10]]}]).scaled(0.5)
    assert z.spec[0]["rect"] == [0.0, 0.0, 50.0, 25.0]
    assert z.spec[1]["polygon"] == [[0.0, 0.0], [5.0, 0.0], [0.0, 5.0]]


@pytest.mark.parametrize("spec", [
    [{"rect": [0, 0, 1, 1]}],                              # no name
    [{"name": "a"}],                                       # neither rect nor polygon
    [{"name": "a", "rect": [0, 0, 1, 1], "polygon": [[0, 0], [1, 0], [0, 1]]}],
    [{"name": "a", "rect": [0, 0, 1]}],                    # short rect
    [{"name": "a", "polygon": [[0, 0], [1, 1]]}],          # only two points
    [{"name": "a", "rect": [0, 0, 1, 1]}, {"name": "a", "rect": [1, 1, 2, 2]}],
])
def test_bad_zone_specs_are_refused(spec):
    with pytest.raises(ZoneConfigError):
        Zones(spec)
