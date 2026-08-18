# Spatiotemporal annotator

Annotate animal video so that **every label carries both an identity and a duration**: not
"this frame contains movement", but "*this* individual was walking from 3.4 s to 5.8 s".

That is what a time budget needs. A per frame classification cannot give it without being
aggregated by something that already knows which animal is which, and a tracking metric
cannot give it either, because a bounding box centroid moves when an animal flaps, preens
or turns in place while staying exactly where it was.

```
sta demo          # four real 20 s broiler clips, no model and no GPU needed
```

<!-- add a screenshot here once the repository is public -->

## What it does

- A tracker proposes one row per individual. **The human disposes.** Click any animal in
  the video and the selected row re-anchors onto it from that frame on, so an identity
  switch costs one click and no box is ever drawn by hand.
- **Hold a key while stepping or playing** to paint a whole interval in one gesture. One
  key confirms an individual that never left the baseline state.
- A **timeline** under the video carries the selected individual's states across the clip.
  Drag it to scrub, drag a boundary between two states to move it, undo anything.
- **Your own ethogram.** States are configured per project, each with its own name,
  colour and paint key.
- **Your own frame rate and frame size.** Resample a 30 fps recording to 5 fps on ingest,
  downscale wide frames, cap how much of each clip is annotated.
- **Your own detector.** Any Ultralytics checkpoint, or a file of boxes from a pipeline
  this tool has never heard of.
- **Optional zones**, so one camera covering two enclosures gives two grid columns and an
  individual is assigned to the enclosure it is standing in.
- **Export** to three shapes: per frame, per bout, and per zone and clip.

Everything lives in one project directory. Zip it, move it between machines, archive it
beside a paper.

## Install

```bash
pip install spatiotemporal-annotator            # core, runs the examples
pip install 'spatiotemporal-annotator[detect]'  # adds ultralytics, to run your own model
```

From a checkout:

```bash
git clone https://github.com/steven068zzy/spatiotemporal-annotator
cd spatiotemporal-annotator
pip install -e '.[dev]'
pytest
```

Python 3.9 or newer. The core needs OpenCV, NumPy and PyYAML. A GPU is needed only if you
run a detector.

## Five minutes

```bash
sta demo                       # builds a project from the bundled clips and serves it
```

Open <http://localhost:8767>. Four real clips are waiting, each with its detections and its
enclosure geometry already in place. Press `A` while holding it and stepping right, and
watch an interval appear on the timeline.

The examples ship their detections, so this runs with no model, no GPU and no network.

## Your own video

```bash
sta init myproject --extract-fps 5 --frame-max-width 1280 --model ~/weights/best.pt
sta add  myproject ~/video/*.mp4 --tag day=1 --tag camera=left
sta serve myproject
sta export myproject
```

Or skip the shell: `sta init myproject && sta serve myproject`, then drop a video onto the
**+ Add video** panel in the browser and set the model under **Settings**. Both routes do
the same thing.

### A custom ethogram

```bash
sta init myproject \
  --state l:lying:baseline \
  --state w:walking:primary \
  --state f:feeding \
  --state p:preening
```

The first state is the baseline, the one a frame holds until somebody paints over it. The
primary state is the one whose fraction the summary panel and the export report first. Each
key is also its paint hotkey, so `c`, `u`, `x`, `z`, `n`, `s`, `h`, `m` and `-` are refused,
with an error naming what took them.

### Zones

```yaml
# project.yaml
zones:
  - {name: penA, rect: [0, 0, 424, 480]}
  - {name: penB, polygon: [[430, 10], [840, 20], [835, 470], [425, 460]]}
```

Coordinates are pixels in the frame **as stored**, which is the frame you see in the
browser, so a zone can be read off a screenshot. Omit `zones` entirely and every individual
lands in a single column.

Several cameras seeing the same enclosures at different pixels? Give those clips their own
geometry, which is recorded per clip:

```bash
sta add myproject 'cam1/*.mp4' --zones-file zones_cam1.yaml --tag camera=cam1
sta add myproject 'cam3/*.mp4' --zones-file zones_cam3.yaml --tag camera=cam3
```

### Bring your own boxes

No supported detector? Supply the boxes directly.

```csv
frame,x1,y1,x2,y2,conf,track_id
0,188,282,221,330,0.91,11
0,405,120,441,161,0.88,12
1,190,281,223,329,0.90,11
```

```bash
sta add myproject clip.mp4 --detections boxes.csv
```

`conf`, `cls` and `track_id` are optional. When `track_id` is present the identities in the
file are used and no association is run, which is how the bundled examples reproduce the
original study's tracker exactly. Drop the column and the built-in tracker runs instead.

## Two modes

| | free (default) | census (`--census-mode`) |
|---|---|---|
| What it is for | label the individuals you care about | build a reference set with no gaps |
| Marking a clip complete | whenever you say so | refused while any row is unresolved |

Census mode is what a benchmark or a ground truth needs: if a clip can be closed with an
unlabelled animal in it, the activity fraction it yields is not the activity fraction of
that enclosure.

## Frame rate and frame size

Three different frame rates, deliberately kept apart:

| Setting | Meaning | When to change it |
|---|---|---|
| the source rate | what the file holds | never, it is read from the video |
| `extract_fps` | the rate you annotate at | a 30 fps recording annotated at 5 fps costs a sixth of the clicks |
| `playback_fps` | how fast the browser plays | taste, and how fast the animals move |

and two sizes:

| Setting | Meaning |
|---|---|
| `frame_max_width` | downscale on ingest. Boxes and zones scale with the frames |
| `display_max_scale` | how far the browser may enlarge a small frame to fill its column |

Both frame settings apply to the **next** ingest, never retroactively. Re-cutting the
frames of a clip that already carries labels would move every box under every label already
made, so it is refused rather than done quietly.

## Export

```bash
sta export myproject --shapes frames bouts units --complete-only
```

| Shape | One row per | Use it for |
|---|---|---|
| `frames` | clip, individual, frame | the complete record, recomputes everything else |
| `bouts` | contiguous run of a state | durations, latencies, bout counts |
| `units` | clip and zone | the group level fraction, the unit a method comparison is scored on |

Rows dismissed as `merged` are excluded from all three: they are the same animal listed
twice. Rows a human `discarded` are excluded too, and counted in the report so the
exclusion is visible.

## How a label is stored

An individual's identity over a clip is a list of segments, and its behaviour is one
character per frame:

```json
{
  "individual_id": "i07",
  "zone": "penA",
  "segments": [{"from": 0, "track_id": 41, "by": "tracker"},
               {"from": 45, "track_id": 57, "by": "human"}],
  "fstate": "rrrrraaaaaarrrrmm----"
}
```

`r` is the baseline, `a` a painted state, `m` a **missed detection**, meaning the human can
see the animal but no box exists for it, and `-` means the frame lies outside this
individual's span. `m` is the label that keeps a detector's failures visible instead of
silently counting them as rest. In the original study it came to 1.83 % of
individual-frames under human audit.

The two characters `m` and `-` are reserved. Everything else is yours.

## The examples

Four 20 s overhead clips of broiler chickens, in weeks 3 to 6 of a grow-out, from two
cameras each covering two pens. They are the four clips of a 42 clip annotated corpus with
the **lowest human audited detector miss rate**, all at or below 0.062 %, each holding at
least 15 active individual-frames so there is something to annotate.

Data under CC BY 4.0, see [`examples/LICENSE-DATA.txt`](examples/LICENSE-DATA.txt).
Collected under Texas A&M University IACUC protocol 2024-0108.

## Limitations, stated plainly

- **Coverage is conditional on detection.** An animal the detector never boxed cannot be
  annotated, only marked `m`. This tool cannot bound detector induced bias by itself.
- **Two animals under one box cannot be separated.** Re-anchoring fixes identity
  *switches*, not a detection covering two individuals at once. Such a pair stays one row.
- **There is no `unsure` state.** An animal you cannot judge falls to the baseline, which
  biases the primary fraction **down**. `m` can serve as a temporary escape hatch.
- **Occlusion is not separable from the baseline.** An animal hidden in a huddle reads as
  resting. Same direction of bias.
- **The server binds to loopback and has no authentication.** It is a local tool. Do not
  expose the port.

## Citation

See [`CITATION.cff`](CITATION.cff).

## Licence

MIT for the software, see [`LICENSE`](LICENSE). CC BY 4.0 for the example data, see
[`examples/LICENSE-DATA.txt`](examples/LICENSE-DATA.txt).
