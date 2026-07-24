#! python3
# Dev-only command: visual checks for the stamp engine against test-geometry.3dm (not published).
#
# Validates non-interactive stamp behavior in one run: normalization accepts a
# planar star and a circle, rejects a non-planar curve, and an array of the two
# stamps (cycled) previews on the picked targets via the conduit. Click-place and
# freehand are interactive-only; exercise them from the panel.

import math

import Rhino
import scriptcontext

from surfacepattern.core import mapping, session
from surfacepattern.engine import stamp

TARGET_DIVISIONS = 8   # array cells along the larger face dimension
STAMP_RATIO = 0.6      # stamp size as a fraction of the lattice step


def _star_curve(points=5, outer=10.0, inner=4.0):
    """Closed planar star polyline used as a registration test stamp."""
    corners = []
    for i in range(points * 2):
        radius = outer if i % 2 == 0 else inner
        angle = math.pi * i / points
        corners.append(
            Rhino.Geometry.Point3d(radius * math.cos(angle), radius * math.sin(angle), 0.0)
        )
    corners.append(corners[0])
    return Rhino.Geometry.PolylineCurve(corners)


def _nonplanar_curve():
    """Closed non-planar curve that normalization must reject."""
    points = [
        Rhino.Geometry.Point3d(0.0, 0.0, 0.0),
        Rhino.Geometry.Point3d(10.0, 0.0, 5.0),
        Rhino.Geometry.Point3d(10.0, 10.0, 0.0),
        Rhino.Geometry.Point3d(0.0, 10.0, 5.0),
        Rhino.Geometry.Point3d(0.0, 0.0, 0.0),
    ]
    return Rhino.Geometry.PolylineCurve(points)


def main():
    """Register built-in test stamps and preview a cycled array on picked targets."""
    tolerance = scriptcontext.doc.ModelAbsoluteTolerance

    star, star_reason = stamp.normalize_stamp(_star_curve(), tolerance)
    circle, circle_reason = stamp.normalize_stamp(
        Rhino.Geometry.Circle(Rhino.Geometry.Plane.WorldXY, 5.0).ToNurbsCurve(), tolerance
    )
    if star is None or circle is None:
        Rhino.RhinoApp.WriteLine(
            "SPDev_TestStamp: FAIL — valid stamps rejected ({}, {}).".format(
                star_reason, circle_reason
            )
        )
        return
    rejected, reject_reason = stamp.normalize_stamp(_nonplanar_curve(), tolerance)
    Rhino.RhinoApp.WriteLine(
        "SPDev_TestStamp: non-planar rejection {} ({}).".format(
            "OK" if rejected is None else "FAIL — accepted", reject_reason
        )
    )

    current = session.get_session()
    if not session.pick_targets(current):
        Rhino.RhinoApp.WriteLine("SPDev_TestStamp: nothing selected.")
        return

    scale = mapping.local_scale(current.targets[0], 0.5, 0.5)
    step = max(scale) / TARGET_DIVISIONS if scale else 10.0
    current.stamps = [star, circle]
    current.manual_placements = []
    current.freehand_strokes = []
    current.params.update(
        {
            "pattern_mode": "stamp",
            "stamp_place_mode": "array",
            "stamp_select": "cycle",
            "stamp_size": step * STAMP_RATIO,
            "stamp_rotation": 0.0,
            "stamp_jitter": 0.0,
            "spacing_x": step * (1.0 - STAMP_RATIO),
            "spacing_y": step * (1.0 - STAMP_RATIO),
            "grid_type": "square",
            "seed": 0,
        }
    )
    current.request_recompute(False)

    Rhino.RhinoApp.WriteLine(
        "SPDev_TestStamp: {} faces, {} preview curves (stamp size ~{:.1f}); "
        "expect alternating star/circle stamps.".format(
            len(current.targets), len(current.preview_curves), step * STAMP_RATIO
        )
    )


main()
