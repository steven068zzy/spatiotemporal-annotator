# Example clips

Four 20 s overhead recordings of broiler chickens, with the detections and track
identities the original study produced for them.

```bash
sta demo            # builds a project from these and opens the annotator
sta demo --blank    # same clips, unannotated, if you would rather do it yourself
```

They ship **the study's own annotation** as well, and `sta demo` loads it, so the tool
opens on finished work rather than on forty unlabelled rows. Every state in it was
assigned by a human watching that bird, not inferred from box displacement.

## Why these four

They are the four clips of a 42 clip annotated corpus with the **lowest human audited
detector miss rate**. In the study each clip was labelled exhaustively, every detected bird
in every frame, and a bird the annotator could see but the detector had missed was marked
`m`. These four sit at or below **0.062 %** missed, and three of them at exactly zero, so
almost nothing in them is an artefact of the detector failing.

Each also holds at least 15 active individual-frames, so there is something to annotate
rather than forty motionless birds.

| clip | week | camera | pens | frames | detections | tracks | missed rate in the study |
|---|---|---|---|---|---|---|---|
| `cam1__20251014_082500` | 3 | cam1 | M2, L2 | 100 | 3,718 | 39 | 0.000 % |
| `cam3__20251021_115500` | 4 | cam3 | S1, M1 | 100 | 3,271 | 39 | 0.032 % |
| `cam3__20251103_091000` | 5 | cam3 | S1, M1 | 100 | 3,198 | 32 | 0.062 % |
| `cam1__20251110_040500` | 6 | cam1 | M2, L2 | 100 | 3,000 | 30 | 0.000 % |

Two cameras, four grow-out weeks, four times of day. Bird body size roughly doubles between
week 3 and week 6, which is worth seeing: it is the reason a fixed pixel speed threshold
fails on a growing flock.

## What is in here

```
examples.json                 the manifest `sta demo` reads
videos/<clip>.mp4             the annotated window only, H.264, 848 x 480 at 5 fps
detections/<clip>.csv         frame,x1,y1,x2,y2,conf,track_id
labels/<clip>.json            the study's annotation, in this tool's label format
zones_cam1.yaml               pen polygons for cam1, for --zones-file
zones_cam3.yaml               pen polygons for cam3
LICENSE-DATA.txt              CC BY 4.0
```

## What the annotation contains

| clip | individuals | active frames | observable frames | active fraction | missed |
|---|---|---|---|---|---|
| `cam1__20251014_082500` | 37 | 18 | 3,700 | 0.0049 | 0 |
| `cam3__20251021_115500` | 31 | 18 | 3,099 | 0.0058 | 1 |
| `cam3__20251103_091000` | 32 | 65 | 3,198 | 0.0203 | 2 |
| `cam1__20251110_040500` | 30 | 20 | 3,000 | 0.0067 | 0 |
| **total** | **130** | **121** | **12,997** | **0.0093** | **3** |

An active fraction near one percent is what an overhead broiler pen actually looks like,
and it is worth seeing before designing a metric: at this base rate a constant "inactive"
prediction is right 99 percent of the time, so accuracy is meaningless and a threshold
tuned on a movement-enriched sample will badly overestimate activity.

`sta export demo-project` turns this into 13,200 frame rows, 12 bout rows and 8 unit rows,
one unit per pen per clip.

### How the annotation was imported

The study's census label files were converted once into this tool's format. `segments` and
the per-frame state strings carry over untouched, because they are the stored truth. Every
derived field, meaning boxes, presence counts, fractions and bouts, is recomputed by
`core.reconcile()` against the tracks this tool ingested, so the imported labels are
consistent with this tool's own geometry rather than trusted from the old file. Row ids
were renumbered `i00, i01, ...` in file order, because the source used two different
schemes.

The videos are re-encoded from the original MPEG-4 recordings and cut to the 100 frame
window the study annotated. Nothing is scaled, so the pen polygons are in the same pixels
as the frames.

## The detections carry track identities

Each CSV has a `track_id` column holding the identity assigned by the study's tracker
(ByteTrack, run per pen, match 0.70, new 0.35, buffer 60, at 5 fps). Because the column is
present, `sta demo` reuses those identities and runs no association of its own, so the demo
reproduces the study's proposal exactly and needs no tracker dependency.

**To watch the built-in tracker work instead**, strip the column and re-ingest:

```bash
cut -d, -f1-6 examples/detections/cam1__20251014_082500.csv > /tmp/no_ids.csv
sta add demo-project examples/videos/cam1__20251014_082500.mp4 \
    --clip retracked --detections /tmp/no_ids.csv \
    --zones-file examples/zones_cam1.yaml
```

Expect more identity switches than the study's tracker made. Repairing one is a click, and
seeing that is the point.

## Provenance

Trial at the Texas A&M University Poultry Science Center, 2025. Six pens of 20 male
broilers (Yield Plus x Ross 708) on non-perforated grooved floor panels, three groove
widths, two pens each. Overhead Intel RealSense D415 cameras 2.5 m above the walking
surface, each covering two pens, RGB at 848 x 480 and 5 fps.

Detector: YOLO11x fine tuned on 720 overhead frames from the same house.

Approved by the Texas A&M University Institutional Animal Care and Use Committee under
protocol IACUC 2024-0108.

Pen labels encode groove width: `S` small, `M` medium, `L` large.
