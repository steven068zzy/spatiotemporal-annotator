"""The state vocabulary a project annotates with.

Two characters are reserved by the annotation model itself and cannot be redefined:

    m   missed    the individual is visible to the human but no box exists for it
    -   no data   the frame lies outside the individual's span

Everything else is the project's own ethogram. Exactly one state is the `baseline`, the
state a frame holds until somebody paints over it, and one painted state is the `primary`
one whose fraction the summary panel and the CSV export report first.

A state's `key` is also its paint hotkey, so the keys the interface needs for other
actions are refused here rather than silently shadowed.
"""

MISSED, NODATA = "m", "-"

RESERVED_STATE_KEYS = {MISSED, NODATA}
# Interface actions that own a letter. A state key may not take one of these.
RESERVED_UI_KEYS = {"c", "u", "x", "z", "n", "s", "h", "?"}

DEFAULT_STATES = [
    {"key": "r", "name": "resting", "baseline": True, "color": "#3C9BC9"},
    {"key": "a", "name": "active", "primary": True, "color": "#F97F5F"},
]

FALLBACK_COLORS = ["#F97F5F", "#65BDBA", "#B58BD8", "#E3B23C", "#7FB069", "#D46A9F"]


class StateConfigError(ValueError):
    pass


class States:
    """A validated ethogram. Immutable once built."""

    def __init__(self, spec=None):
        spec = [dict(s) for s in (spec or DEFAULT_STATES)]
        if not spec:
            raise StateConfigError("a project needs at least one state")
        self._validate_keys(spec)
        self.states = self._assign_roles(spec)
        self.by_key = {s["key"]: s for s in self.states}
        self.rest = next(s["key"] for s in self.states if s.get("baseline"))
        self.active = next(s["key"] for s in self.states if s.get("primary"))
        self.painted = tuple(s["key"] for s in self.states)
        self.observable = self.painted
        self.paintable = self.painted + (MISSED,)

    @staticmethod
    def _validate_keys(spec):
        seen = set()
        for s in spec:
            key = str(s.get("key", ""))
            if len(key) != 1:
                raise StateConfigError(
                    "state key must be exactly one character, got %r" % (key,))
            if key in RESERVED_STATE_KEYS:
                raise StateConfigError(
                    "state key %r is reserved by the annotation model "
                    "('m' missed, '-' no data)" % key)
            if key.lower() in RESERVED_UI_KEYS:
                raise StateConfigError(
                    "state key %r is reserved by an interface action, pick another "
                    "letter (taken: %s)" % (key, " ".join(sorted(RESERVED_UI_KEYS))))
            if key in seen:
                raise StateConfigError("duplicate state key %r" % key)
            if not s.get("name"):
                raise StateConfigError("state %r needs a name" % key)
            seen.add(key)

    @staticmethod
    def _assign_roles(spec):
        """Fill in baseline, primary and colour when the project left them out."""
        if not any(s.get("baseline") for s in spec):
            spec[0]["baseline"] = True
        if sum(1 for s in spec if s.get("baseline")) > 1:
            raise StateConfigError("exactly one state may be the baseline")
        painted = [s for s in spec if not s.get("baseline")]
        if not painted:
            raise StateConfigError(
                "a project needs at least one state besides the baseline, "
                "otherwise there is nothing to annotate")
        if not any(s.get("primary") for s in spec):
            painted[0]["primary"] = True
        if sum(1 for s in spec if s.get("primary")) > 1:
            raise StateConfigError("at most one state may be primary")
        if next(s for s in spec if s.get("primary")).get("baseline"):
            raise StateConfigError("the baseline state cannot also be the primary state")
        n = 0
        for s in spec:
            if not s.get("color"):
                if s.get("baseline"):
                    s["color"] = "#3C9BC9"
                else:
                    s["color"] = FALLBACK_COLORS[n % len(FALLBACK_COLORS)]
                    n += 1
        return spec

    def as_dict(self):
        """What core.py's helpers need, and what the browser is handed."""
        return {"states": self.states, "rest": self.rest, "active": self.active,
                "painted": list(self.painted), "observable": list(self.observable),
                "paintable": list(self.paintable),
                "missed": MISSED, "nodata": NODATA}

    def name_of(self, key):
        if key == MISSED:
            return "missed"
        if key == NODATA:
            return "no data"
        return self.by_key[key]["name"]

    def __len__(self):
        return len(self.states)

    def __repr__(self):
        return "States(%s)" % ", ".join("%s=%s" % (s["key"], s["name"])
                                        for s in self.states)
