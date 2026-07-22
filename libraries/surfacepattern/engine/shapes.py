#! python3
# Unit shape definitions shared by engines: closed NURBS curve + polyline draft approximation.

import math

import Rhino

DRAFT_SEGMENTS = 12       # polyline segments for the draft circle
CAP_SEGMENTS = 4          # polyline segments per slot end cap

SHAPE_KINDS = ("circle", "slot", "hex")


def _polyline(points):
    """Closed PolylineCurve from a point list (first point appended as closer)."""
    closed = list(points) + [points[0]]
    return Rhino.Geometry.PolylineCurve(
        [Rhino.Geometry.Point3d(x, y, 0.0) for x, y in closed]
    )


def unit_circle():
    """Circle of diameter 1 at the origin: (nurbs, draft_polyline)."""
    nurbs = Rhino.Geometry.Circle(Rhino.Geometry.Plane.WorldXY, 0.5).ToNurbsCurve()
    points = []
    for i in range(DRAFT_SEGMENTS):
        angle = 2.0 * math.pi * i / DRAFT_SEGMENTS
        points.append((0.5 * math.cos(angle), 0.5 * math.sin(angle)))
    return nurbs, _polyline(points)


def unit_slot(width_ratio=0.4):
    """Stadium slot, length 1 along X, width = width_ratio: (nurbs, draft_polyline)."""
    ratio = min(max(width_ratio, 0.01), 1.0)
    if ratio >= 1.0:
        return unit_circle()
    radius = 0.5 * ratio
    half = 0.5 - radius  # cap center offset from the origin

    right_cap = Rhino.Geometry.Arc(
        Rhino.Geometry.Point3d(half, -radius, 0.0),
        Rhino.Geometry.Point3d(half + radius, 0.0, 0.0),
        Rhino.Geometry.Point3d(half, radius, 0.0),
    )
    left_cap = Rhino.Geometry.Arc(
        Rhino.Geometry.Point3d(-half, radius, 0.0),
        Rhino.Geometry.Point3d(-half - radius, 0.0, 0.0),
        Rhino.Geometry.Point3d(-half, -radius, 0.0),
    )
    segments = [
        Rhino.Geometry.ArcCurve(right_cap),
        Rhino.Geometry.LineCurve(
            Rhino.Geometry.Point3d(half, radius, 0.0),
            Rhino.Geometry.Point3d(-half, radius, 0.0),
        ),
        Rhino.Geometry.ArcCurve(left_cap),
        Rhino.Geometry.LineCurve(
            Rhino.Geometry.Point3d(-half, -radius, 0.0),
            Rhino.Geometry.Point3d(half, -radius, 0.0),
        ),
    ]
    joined = Rhino.Geometry.Curve.JoinCurves(segments)
    nurbs = joined[0].ToNurbsCurve()

    points = []
    for i in range(CAP_SEGMENTS + 1):  # right cap, -90 -> +90 degrees
        angle = -0.5 * math.pi + math.pi * i / CAP_SEGMENTS
        points.append((half + radius * math.cos(angle), radius * math.sin(angle)))
    for i in range(CAP_SEGMENTS + 1):  # left cap, +90 -> +270 degrees
        angle = 0.5 * math.pi + math.pi * i / CAP_SEGMENTS
        points.append((-half + radius * math.cos(angle), radius * math.sin(angle)))
    return nurbs, _polyline(points)


def unit_hexagon():
    """Regular hexagon with circumscribed diameter 1: (nurbs, draft_polyline)."""
    points = []
    for i in range(6):
        angle = 2.0 * math.pi * i / 6.0
        points.append((0.5 * math.cos(angle), 0.5 * math.sin(angle)))
    polyline = _polyline(points)
    return polyline.ToNurbsCurve(), polyline


def unit_shape(kind, slot_ratio=0.4):
    """Return (nurbs, draft_polyline) for 'circle' | 'slot' | 'hex'; unknown kinds fall back to circle."""
    if kind == "slot":
        return unit_slot(slot_ratio)
    if kind == "hex":
        return unit_hexagon()
    return unit_circle()
