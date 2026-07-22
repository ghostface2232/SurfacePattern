#! python3
# Dev-only command: visual checks for the preview DisplayConduit (not published).

import math

import Rhino
import scriptcontext

from surfacepattern.core import mapping, session
from surfacepattern.preview import conduit

STATE_KEY = "surfacepattern_testconduit_state"  # "off" | "draft" | "full"

TARGET_DIVISIONS = 15   # grid cells along the larger face dimension
HOLE_RATIO = 0.5        # hole diameter as a fraction of grid spacing
DRAFT_SEGMENTS = 10     # polyline segments approximating the draft circle


def grid_counts_for_face(record):
    """Derive (u_count, v_count, spacing_3d) from the face's physical size at its center."""
    scale = mapping.local_scale(record, 0.5, 0.5)
    if scale is None:
        return 10, 10, 0.0
    size_u, size_v = scale
    spacing = max(size_u, size_v) / TARGET_DIVISIONS
    if spacing <= 0.0:
        return 10, 10, 0.0
    u_count = max(2, int(round(size_u / spacing)))
    v_count = max(2, int(round(size_v / spacing)))
    return u_count, v_count, spacing


def unit_circle_nurbs():
    """Unit circle (diameter 1, origin, XY plane) as a NURBS curve."""
    return Rhino.Geometry.Circle(Rhino.Geometry.Plane.WorldXY, 0.5).ToNurbsCurve()


def unit_circle_polyline():
    """Unit circle approximated as a closed polyline with DRAFT_SEGMENTS segments."""
    points = []
    for i in range(DRAFT_SEGMENTS + 1):
        angle = 2.0 * math.pi * i / DRAFT_SEGMENTS
        points.append(Rhino.Geometry.Point3d(0.5 * math.cos(angle), 0.5 * math.sin(angle), 0.0))
    return Rhino.Geometry.PolylineCurve(points)


def build_preview(current):
    """Fill the session preview caches (full pulled NURBS + flat draft polylines)."""
    nurbs_unit = unit_circle_nurbs()
    draft_unit = unit_circle_polyline()
    full_curves = []
    draft_curves = []
    fallbacks = 0
    for record in current.targets:
        u_count, v_count, spacing = grid_counts_for_face(record)
        if spacing <= 0.0:
            continue
        hole_diameter = spacing * HOLE_RATIO
        for u, v in mapping.uv_grid(record, u_count, v_count):
            flat = mapping.place_unit_curve_flat(record, u, v, draft_unit, hole_diameter, 0.0)
            if flat is not None:
                draft_curves.append(flat)
            curve, pulled = mapping.place_unit_curve(record, u, v, nurbs_unit, hole_diameter, 0.0)
            if curve is not None:
                full_curves.append(curve)
                if not pulled:
                    fallbacks += 1
    current.set_preview(full_curves, draft_curves)
    return fallbacks


def main():
    """Cycle the preview on re-run: off -> draft -> full -> off. Never writes to the document.

    The cycle state lives in scriptcontext.sticky as a plain string so it does not
    depend on the conduit instance surviving between runs.
    """
    current = session.get_session()
    preview = conduit.get_conduit()
    state = scriptcontext.sticky.get(STATE_KEY, "off")

    # Diagnostics: if conduit# changes between runs, sticky is not persisting instances;
    # if state stays "off" on every run, sticky is not persisting at all.
    Rhino.RhinoApp.WriteLine(
        "SPDev_TestConduit: [diag] state={}, conduit#{}, enabled={}, cached draft/full={}/{}".format(
            state,
            id(preview) % 100000,
            preview.Enabled,
            len(current.preview_draft_curves),
            len(current.preview_curves),
        )
    )

    # If the conduit instance was recreated between runs, re-enable the new one and
    # keep cycling — the curve caches live in the session, not the conduit.
    if state != "off" and not preview.Enabled:
        Rhino.RhinoApp.WriteLine("SPDev_TestConduit: [diag] conduit was lost between runs, re-enabling.")
        preview.enable()

    if state == "off":
        if not session.pick_targets(current):
            Rhino.RhinoApp.WriteLine("SPDev_TestConduit: nothing selected.")
            return
        fallbacks = build_preview(current)
        current.preview_quality = "draft"
        preview.enable()
        scriptcontext.sticky[STATE_KEY] = "draft"
        Rhino.RhinoApp.WriteLine(
            "SPDev_TestConduit: DRAFT preview on ({} polylines, {} full curves cached, "
            "{} pullback fallbacks). Run again for FULL.".format(
                len(current.preview_draft_curves), len(current.preview_curves), fallbacks
            )
        )
    elif state == "draft":
        current.preview_quality = "full"
        preview.enable()  # already on; forces a redraw
        scriptcontext.sticky[STATE_KEY] = "full"
        Rhino.RhinoApp.WriteLine(
            "SPDev_TestConduit: FULL preview ({} NURBS curves). Run again to turn off.".format(
                len(current.preview_curves)
            )
        )
    else:
        preview.disable()
        current.clear_preview()
        current.preview_quality = "draft"
        scriptcontext.sticky[STATE_KEY] = "off"
        Rhino.RhinoApp.WriteLine("SPDev_TestConduit: preview off.")


main()
