"""Optional spatial zones, the generalisation of the pens in the original study.

A project that defines zones gets one grid column per zone, and every individual is
assigned to the zone holding the centre of its first box. A project that defines none
gets a single column and every individual counts, which is the right default for a single
enclosure filmed by a single camera.

A zone is either a rectangle

    {"name": "penA", "rect": [x1, y1, x2, y2]}

or a polygon in image coordinates

    {"name": "arenaLeft", "polygon": [[x, y], [x, y], ...]}

Coordinates are pixels in the frame as stored, so they follow `frame_max_width` when a
project downscales on extraction. `Zones.scaled()` rewrites them when that changes.
"""


class ZoneConfigError(ValueError):
    pass


def _centre(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _in_rect(pt, rect):
    x, y = pt
    x1, y1, x2, y2 = rect
    return min(x1, x2) <= x <= max(x1, x2) and min(y1, y2) <= y <= max(y1, y2)


def _in_polygon(pt, poly):
    """Ray casting. A point exactly on an edge may fall either way, which is acceptable
    for assigning an animal to an enclosure."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            t = (y - y1) / (y2 - y1)
            if x < x1 + t * (x2 - x1):
                inside = not inside
    return inside


class Zones:
    def __init__(self, spec=None):
        self.spec = [dict(z) for z in (spec or [])]
        names = set()
        for z in self.spec:
            name = z.get("name")
            if not name:
                raise ZoneConfigError("every zone needs a name")
            if name in names:
                raise ZoneConfigError("duplicate zone name %r" % name)
            names.add(name)
            has_rect, has_poly = "rect" in z, "polygon" in z
            if has_rect == has_poly:
                raise ZoneConfigError(
                    "zone %r needs exactly one of 'rect' or 'polygon'" % name)
            if has_rect and len(z["rect"]) != 4:
                raise ZoneConfigError("zone %r rect must be [x1, y1, x2, y2]" % name)
            if has_poly and len(z["polygon"]) < 3:
                raise ZoneConfigError("zone %r polygon needs at least 3 points" % name)

    def __bool__(self):
        return bool(self.spec)

    def __len__(self):
        return len(self.spec)

    @property
    def names(self):
        return [z["name"] for z in self.spec]

    def of_point(self, pt):
        """The first zone containing `pt`, or None. Order in the config breaks overlaps."""
        for z in self.spec:
            if "rect" in z:
                if _in_rect(pt, z["rect"]):
                    return z["name"]
            elif _in_polygon(pt, z["polygon"]):
                return z["name"]
        return None

    def of_box(self, box):
        return self.of_point(_centre(box))

    def of_track(self, frame_boxes):
        """The zone of a track, from its first box. {frame: box} in, zone name out.

        The first box rather than a majority vote: an animal that walks out of frame
        should keep the zone it was annotated in, and a majority vote would silently
        move a row between grid columns as annotation proceeds.
        """
        if not frame_boxes:
            return None
        return self.of_box(frame_boxes[min(frame_boxes)])

    def scaled(self, factor):
        """The same zones in coordinates scaled by `factor`."""
        out = []
        for z in self.spec:
            z = dict(z)
            if "rect" in z:
                z["rect"] = [round(v * factor, 2) for v in z["rect"]]
            else:
                z["polygon"] = [[round(x * factor, 2), round(y * factor, 2)]
                                for x, y in z["polygon"]]
            out.append(z)
        return Zones(out)

    def as_list(self):
        return [dict(z) for z in self.spec]
