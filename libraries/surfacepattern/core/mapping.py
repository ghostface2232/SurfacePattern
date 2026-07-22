#! python3
# UV<->3D conversion, surface frames, distortion compensation, world-grid projection, curve pullback.

import Rhino
import scriptcontext


def _denormalize(face_record, u, v):
    """Map normalized (0-1) UV to the face's raw surface parameters."""
    u0, u1 = face_record.domain_u
    v0, v1 = face_record.domain_v
    return u0 + (u1 - u0) * u, v0 + (v1 - v0) * v


def _normalize(face_record, su, sv):
    """Map raw surface parameters back to normalized (0-1) UV."""
    u0, u1 = face_record.domain_u
    v0, v1 = face_record.domain_v
    return (su - u0) / (u1 - u0), (sv - v0) / (v1 - v0)


def uv_grid(face_record, u_count, v_count):
    """Uniform normalized-UV grid over the face, culling points outside the trimmed region."""
    face = face_record.resolve_face()
    if face is None:
        return []
    points = []
    for i in range(u_count):
        u = (i + 0.5) / u_count
        for j in range(v_count):
            v = (j + 0.5) / v_count
            su, sv = _denormalize(face_record, u, v)
            if face.IsPointOnFace(su, sv) == Rhino.Geometry.PointFaceRelation.Exterior:
                continue
            points.append((u, v))
    return points


def local_frame(face_record, u, v):
    """Return the surface frame (Plane) at normalized (u, v) with Z pointing outward; None on failure."""
    su, sv = _denormalize(face_record, u, v)
    ok, frame = face_record.surface.FrameAt(su, sv)
    if not ok:
        return None
    if face_record.orientation_reversed:
        # Flip Z while keeping a right-handed frame: negate Y, keep X.
        frame = Rhino.Geometry.Plane(frame.Origin, frame.XAxis, -frame.YAxis)
    return frame


def local_scale(face_record, u, v):
    """Return (scale_u, scale_v): 3D arc length of one normalized UV unit at (u, v); None on failure."""
    su, sv = _denormalize(face_record, u, v)
    ok, _point, derivatives = face_record.surface.Evaluate(su, sv, 1)
    if not ok or derivatives is None or len(derivatives) < 2:
        return None
    span_u = abs(face_record.domain_u[1] - face_record.domain_u[0])
    span_v = abs(face_record.domain_v[1] - face_record.domain_v[0])
    return derivatives[0].Length * span_u, derivatives[1].Length * span_v


def world_grid_projection(face_records, spacing, plane, max_distance=None):
    """Project a planar world grid onto the nearest face; returns [(face_record, u, v), ...] normalized.

    Grid extent covers the union bounding box of all faces expressed in plane coordinates.
    A point is kept only for its closest face, only if it lands on the trimmed region, and
    only if the projection distance stays under max_distance (default: half the bounding-box
    diagonal, floored at document tolerance) — far misses pulled to face borders are culled.
    """
    if spacing <= 0.0 or not face_records:
        return []
    tolerance = scriptcontext.doc.ModelAbsoluteTolerance

    resolved = []
    bbox = Rhino.Geometry.BoundingBox.Empty
    for record in face_records:
        face = record.resolve_face()
        if face is None:
            continue
        resolved.append((record, face))
        bbox.Union(face.GetBoundingBox(True))
    if not resolved or not bbox.IsValid:
        return []

    if max_distance is None:
        max_distance = max(bbox.Diagonal.Length * 0.5, tolerance)

    # Grid extent: bounding-box corners expressed in plane (s, t) coordinates.
    s_values, t_values = [], []
    for corner in bbox.GetCorners():
        ok, s, t = plane.ClosestParameter(corner)
        if ok:
            s_values.append(s)
            t_values.append(t)
    if not s_values:
        return []

    placements = []
    s_count = int((max(s_values) - min(s_values)) / spacing) + 1
    t_count = int((max(t_values) - min(t_values)) / spacing) + 1
    for i in range(s_count + 1):
        for j in range(t_count + 1):
            grid_point = plane.PointAt(min(s_values) + i * spacing, min(t_values) + j * spacing)
            best = None
            for record, face in resolved:
                ok, su, sv = face.ClosestPoint(grid_point)
                if not ok:
                    continue
                if face.IsPointOnFace(su, sv) == Rhino.Geometry.PointFaceRelation.Exterior:
                    continue
                distance = grid_point.DistanceTo(record.surface.PointAt(su, sv))
                if best is None or distance < best[0]:
                    best = (distance, record, su, sv)
            if best is not None and best[0] <= max_distance:
                _distance, record, su, sv = best
                u, v = _normalize(record, su, sv)
                placements.append((record, u, v))
    return placements


def place_unit_curve(face_record, u, v, unit_curve, size, rotation):
    """Orient a unit curve (XY plane, origin-centered) onto the face at normalized (u, v).

    Scales by size, rotates by rotation (radians) about the local normal, then pulls the
    curve onto the BrepFace. Returns (curve, pulled): pulled is False when pullback failed
    and the returned curve is the planar fallback lying on the local frame. (None, False)
    when the frame itself cannot be evaluated.
    """
    frame = local_frame(face_record, u, v)
    if frame is None:
        return None, False

    curve = unit_curve.DuplicateCurve()
    scale = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, size)
    rotate = Rhino.Geometry.Transform.Rotation(
        rotation, Rhino.Geometry.Vector3d.ZAxis, Rhino.Geometry.Point3d.Origin
    )
    orient = Rhino.Geometry.Transform.PlaneToPlane(Rhino.Geometry.Plane.WorldXY, frame)
    curve.Transform(orient * rotate * scale)

    face = face_record.resolve_face()
    if face is not None:
        tolerance = scriptcontext.doc.ModelAbsoluteTolerance
        pulled = curve.PullToBrepFace(face, tolerance)
        if pulled is not None and len(pulled) > 0:
            if len(pulled) == 1:
                return pulled[0], True
            joined = Rhino.Geometry.Curve.JoinCurves(pulled, tolerance)
            if joined is not None and len(joined) > 0:
                return joined[0], True
            return pulled[0], True
    return curve, False
