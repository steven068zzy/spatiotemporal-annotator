"""A spatiotemporal video annotation tool: every label carries an identity and a duration.

The unit of annotation is not a frame and not a clip. It is one individual over one
interval, so a label answers two questions at once, which animal and for how long. That is
what a time budget needs, and it is what a per frame classification cannot give without
being aggregated by something that already knows the identities.

Typical use:

    from spatiotemporal_annotator import Project, ingest_video, serve

    p = Project.create("myproject", extract_fps=5, frame_max_width=848)
    ingest_video(p, "clip.mp4")
    serve(p.root)

Or from a shell:

    sta demo                      # a project built from the bundled example clips
    sta init myproject
    sta add myproject video.mp4 --tag day=1
    sta serve myproject
    sta export myproject
"""

__version__ = "0.1.0"

from .core import (ACTIVE, MISSED, NODATA, REST, active_frac, bouts, overlay,
                   paint_range, reanchor, reconcile)
# Exposed as export_labels, not as `export`. A package attribute and a submodule cannot
# share a name: binding the function here as `export` would shadow the `export` module and
# break `from spatiotemporal_annotator import export`.
from .export import export as export_labels
from .ingest import ingest_many, ingest_video
from .project import Project, ProjectError
from .server import make_server, serve
from .states import States, StateConfigError
from .zones import Zones, ZoneConfigError

__all__ = [
    "__version__",
    "Project", "ProjectError", "States", "StateConfigError", "Zones", "ZoneConfigError",
    "ingest_video", "ingest_many", "serve", "make_server", "export_labels",
    "reconcile", "reanchor", "overlay", "paint_range", "bouts", "active_frac",
    "REST", "ACTIVE", "MISSED", "NODATA",
]
