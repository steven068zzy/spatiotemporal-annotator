# On-disk format

Every file a project writes, and what depends on what. Read this before writing a script
against a project directory.

```
myproject/
  project.yaml            configuration, the only file a human edits by hand
  videos/                 source videos, copied in on `sta add` unless --no-copy
  models/                 checkpoints uploaded through the browser
  detections/             detection files uploaded through the browser
  clips/<clip_id>/
    meta.json             what the ingest produced and how
    frames/00000.jpg      one JPEG per annotation frame
    tracks.json           the tracker's proposal, never rewritten after ingest
  labels/<clip_id>.json   the annotation. This is the only file that holds human work
  labels/<clip_id>__SKIP.json   a marker written by "Skip clip"
  exports/                CSV written by `sta export`
```

## What is authoritative

`labels/<clip_id>.json` is the human record. `tracks.json` seeds it and is then only a
provenance record: after a re-anchor the label file, not the tracks file, is the authority
on which track holds which frame. `store.tracks_of()` rebuilds the association from the
label file for exactly this reason.

Deleting a label file discards annotation. Deleting a clip directory discards frames and
the tracker proposal, which `sta add --overwrite` can rebuild.

## clips/<clip_id>/meta.json

```json
{
  "clip": "cam1__20251014_082500",
  "video": "20251014_082500.mp4",
  "n_frames": 100,
  "w": 848, "h": 480, "fps": 5.0,
  "n_detections": 3718,
  "n_tracks": 39,
  "detector": {"backend": "precomputed", "path": "...", "carries_track_ids": true},
  "tracker":  {"kind": "from_detections_file"},
  "zones":    [{"name": "M2", "polygon": [[193, 214], "..."]}],
  "states":   ["r", "a"],
  "tags":     {"week": "3", "camera": "cam1"},
  "source": {
    "path": "/abs/path/to/source.mp4",
    "n_frames": 122, "w": 848, "h": 480, "fps": 5.0,
    "stride": 1, "scale": 1.0,
    "frame_index": [0, 1, 2, "..."]
  }
}
```

`source.stride` and `source.scale` are what make a clip reproducible. `stride` is how many
source frames one annotation frame advances, so annotation frame `i` is source frame
`source.frame_index[i]`. `scale` is the factor applied to every pixel coordinate, so a box
in the label file times `1 / scale` is a box in the source video.

`zones` is recorded per clip, not read from the project, because `sta add --zones-file`
can give different geometry to different cameras.

## labels/<clip_id>.json

```json
{
  "clip": "cam1__20251014_082500",
  "n_frames": 100, "fps": 5.0, "w": 848, "h": 480,
  "zones": ["L2", "M2"],
  "states": [{"key": "r", "name": "resting", "baseline": true, "color": "#3C9BC9"},
             {"key": "a", "name": "active", "primary": true, "color": "#F97F5F"}],
  "annotator": "steven",
  "tool_version": "0.1.0",
  "complete": false,
  "individuals": [ "..." ]
}
```

The `states` block is copied into every label file. A project whose ethogram changes later
therefore does not silently reinterpret work already done: each file says what its own
characters meant.

`zones` being non-empty is what makes a row need a zone to count as work. It is a property
of the clip, not of the project.

### one individual

```json
{
  "individual_id": "i07",
  "seed_track_id": 100041,
  "zone": "M2",
  "status": "confirmed",
  "source": "tracker",
  "merged_into": null,
  "needs_review": false,
  "note": "",
  "segments": [{"from": 0, "track_id": 100041, "by": "tracker"},
               {"from": 45, "track_id": 100057, "by": "human"}],
  "fstate": "rrrrraaaaaarrrrmm----",
  "boxes": [[188, 282, 221, 330], null, "..."],
  "n_present": 96,
  "state_fracs": {"r": 0.94, "a": 0.06},
  "active_frac": 0.06,
  "bouts": [{"start": 5, "end": 10, "t0": 1.0, "t1": 2.0}]
}
```

`segments` and `fstate` are the stored truth. `boxes`, `n_present`, `state_fracs`,
`active_frac` and `bouts` are **derived** and recomputed by `core.reconcile()` on every
write, so a script may read them but must never be the only place a change is recorded.

`status` is one of:

| status | meaning | how it got there |
|---|---|---|
| `unseen` | not looked at | the initial state of every row |
| `confirmed` | the human accepts this row as painted | `C`, or painting anything |
| `discarded` | the human says this row is not an animal | `X` |
| `merged` | the tool's own dismissal: the row holds no boxes | a re-anchor took them all |

`source` is `tracker` for a seeded row and `auto_split` for a row `reconcile()` created to
adopt boxes a re-anchor orphaned. An `auto_split` row carries no zone, so it is drawn on the
video but not counted as work until a human re-anchors it.

### per-frame characters

| char | meaning |
|---|---|
| the baseline key | the default state, `r` unless the project says otherwise |
| any other state key | painted by a human |
| `m` | missed detection: the animal is visible, no box exists |
| `-` | no data: this frame is outside the individual's span |

`m` may be painted outside the span, because a track that died does not mean the animal
left. A behavioural state may not, because judging a state is what a box is for.

## Invariant to check after any bulk operation

One box belongs to exactly one live individual. Anything that edits label files by hand
should be followed by:

```bash
python3 - <<'PY'
import glob, json, sys
from spatiotemporal_annotator import core as cc
bad = 0
for p in glob.glob("myproject/labels/*.json"):
    if p.endswith("__SKIP.json"):
        continue
    d = json.load(open(p))
    if "individuals" not in d:
        continue
    own = {}
    for b in d["individuals"]:
        if b["status"] == "merged":
            continue
        assert len(b["fstate"]) == d["n_frames"], p
        for f, box in enumerate(b.get("boxes") or []):
            if box is None:
                continue
            k = (cc.active_track(b["segments"], f), f)
            if k in own:
                print("%s: %s owned by both %s and %s" % (p, k, own[k], b["individual_id"]))
                bad += 1
            own[k] = b["individual_id"]
print("ownership and length invariants hold" if not bad else "%d violation(s)" % bad)
sys.exit(1 if bad else 0)
PY
```

## HTTP endpoints

The browser is a client of these, and so can a script be. The server binds to loopback and
has no authentication.

| method | path | does |
|---|---|---|
| GET | `/api/config` | project settings, states, playback options |
| GET | `/api/clips` | one entry per clip with its progress and tags |
| GET | `/api/clip/<id>` | the clip document, seeding it on first open |
| GET | `/api/stats` | corpus-wide progress |
| GET | `/api/jobs` | ingest jobs started through the browser |
| GET | `/frame/<id>/<nnnnn>.jpg` | one frame |
| POST | `/api/paint` | `{clip, individual_id, fstate}` |
| POST | `/api/confirm` | `{clip, individual_id, confirmed}` |
| POST | `/api/discard` | `{clip, individual_id, discarded}` |
| POST | `/api/reanchor` | `{clip, individual_id, frame, track_id}` |
| POST | `/api/note` | `{clip, individual_id?, note}` |
| POST | `/api/complete` | `{clip}`, refused in census mode while a row is open |
| POST | `/api/skip`, `/api/unskip` | `{clip}` |
| POST | `/api/upload?kind=video\|model\|detections&name=<file>` | the raw file as the body |
| POST | `/api/ingest` | `{video, clip, detections, tags, overwrite}`, returns a job id |
| POST | `/api/settings` | the settings the browser may change |
| POST | `/api/export` | `{shapes, complete_only}` |

`/api/paint` refuses a state string of the wrong length or holding a character outside the
project's vocabulary, so a buggy client cannot corrupt a clip.
