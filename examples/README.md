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

Chosen for **a high annotated activity fraction first**, then for **the number of distinct
individuals that move**, then for **a low human audited detector miss rate**. An example
set where almost nothing moves demonstrates the interface but not what the interface is
for, which is what the first cut of these examples got wrong.

| clip | week | camera | pens | individuals | that move | active frames | observable | active fraction | missed |
|---|---|---|---|---|---|---|---|---|---|
| `cam1__20250930_095000` | 1 | cam1 | M2, L2 | 40 | 18 | 254 | 3,797 | 0.0669 | 2.09 % |
| `cam3__20251007_060500` | 2 | cam3 | S1, M1 | 39 | 17 | 278 | 3,718 | 0.0748 | 1.72 % |
| `cam3__20251014_000500` | 3 | cam3 | S1, M1 | 36 | 11 | 174 | 3,542 | 0.0491 | 1.61 % |
| `cam1__20251014_141000` | 3 | cam1 | M2, L2 | 37 | 9 | 143 | 3,670 | 0.0390 | 0.81 % |
| **total** | | | | **152** | **55** | **849** | **14,727** | **0.0576** | **1.56 %** |

`that move` is the number of individuals with at least one active frame, which matters more
than the fraction alone: a clip where one bird paces for 20 s and 38 sit still has a
respectable fraction and nothing to learn from.

The four span weeks 1 to 3 of the grow-out, two cameras, and morning, midday and night. In
the study each clip was labelled exhaustively, every detected bird in every frame, and a
bird the annotator could see but the detector had missed was marked `m`; that is where the
missed rate comes from.

Activity falls steeply with age in this flock, so the highest activity sits in the early
weeks. Later weeks are quieter by roughly an order of magnitude, which is a real finding
about broilers and not a property of these clips.

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

`sta export demo-project` turns the bundled labels into 15,457 frame rows, 198 bout rows
and 8 unit rows, one unit per pen per clip. Five rows carry no zone, so they appear in the
frame and bout exports but belong to no unit; `sta export` says so rather than letting the
row counts quietly fail to add up.

An active fraction near six percent is what an overhead broiler pen looks like in the first
weeks of a grow-out. It is worth seeing before designing a metric: even here the resting
state outnumbers the active one by fifteen to one, so accuracy is close to meaningless and
a threshold tuned on a movement-enriched sample will overestimate activity badly.

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
