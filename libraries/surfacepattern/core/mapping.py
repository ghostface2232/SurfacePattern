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


def is_point_on_face(face_record, u, v):
    """True when normalized (u, v) lies on the trimmed region of the face."""
    face = face_record.resolve_face()
    if face is None:
        return False
    su, sv = _denormalize(face_record, u, v)
    return face.IsPointOnFace(su, sv) != Rhino.Geometry.PointFaceRelation.Exterior


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


def point_at(face_record, u, v):
    """3D surface point at normalized (u, v)."""
    su, sv = _denormalize(face_record, u, v)
    return face_record.surface.PointAt(su, sv)


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


def _resolve_with_bbox(face_records):
    """Resolve live faces and their union bounding box: ([(record, face), ...], bbox)."""
    resolved = []
    bbox = Rhino.Geometry.BoundingBox.Empty
    for record in face_records:
        face = record.resolve_face()
        if face is None:
            continue
        resolved.append((record, face))
        bbox.Union(face.GetBoundingBox(True))
    return resolved, bbox


def default_projection_plane(face_records):
    """Axis-aligned plane through the bbox center facing its widest side; None without targets."""
    _resolved, bbox = _resolve_with_bbox(face_records)
    if not bbox.IsValid:
        return None
    extent = bbox.Max - bbox.Min
    center = bbox.Center
    if extent.Z <= extent.X and extent.Z <= extent.Y:
        normal = Rhino.Geometry.Vector3d.ZAxis
    elif extent.Y <= extent.X:
        normal = Rhino.Geometry.Vector3d.YAxis
    else:
        normal = Rhino.Geometry.Vector3d.XAxis
    return Rhino.Geometry.Plane(center, normal)


def plane_grid_extent(face_records, plane):
    """Targets' bbox expressed in plane coordinates: (s_min, s_max, t_min, t_max); None if empty."""
    _resolved, bbox = _resolve_with_bbox(face_records)
    if not bbox.IsValid:
        return None
    s_values, t_values = [], []
    for corner in bbox.GetCorners():
        ok, s, t = plane.ClosestParameter(corner)
        if ok:
            s_values.append(s)
            t_values.append(t)
    if not s_values:
        return None
    return min(s_values), max(s_values), min(t_values), max(t_values)


def project_plane_points(face_records, plane, points, max_distance=None):
    """Project plane-space (s, t) points onto the closest face.

    Returns a list aligned with the input: (face_record, u, v) with normalized UV for
    projected points, None for culled ones. A point is kept only for its closest face,
    only when it lands on the trimmed region, and only when the projection distance
    stays under max_distance (default: half the targets' bbox diagonal, floored at
    document tolerance) — far misses pulled to face borders are culled.
    """
    resolved, bbox = _resolve_with_bbox(face_records)
    if not resolved or not bbox.IsValid:
        return [None] * len(points)
    if max_distance is None:
        max_distance = max(
            bbox.Diagonal.Length * 0.5, scriptcontext.doc.ModelAbsoluteTolerance
        )

    results = []
    for s, t in points:
        grid_point = plane.PointAt(s, t)
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
            results.append((record, u, v))
        else:
            results.append(None)
    return results


def world_grid_projection(face_records, spacing, plane, max_distance=None):
    """Project a square planar world grid onto the nearest face; [(face_record, u, v), ...]."""
    if spacing <= 0.0 or not face_records:
        return []
    extent = plane_grid_extent(face_records, plane)
    if extent is None:
        return []
    s_min, s_max, t_min, t_max = extent
    points = []
    s_count = int((s_max - s_min) / spacing) + 1
    t_count = int((t_max - t_min) / spacing) + 1
    for i in range(s_count + 1):
        for j in range(t_count + 1):
            points.append((s_min + i * spacing, t_min + j * spacing))
    return [hit for hit in project_plane_points(face_records, plane, points, max_distance) if hit]


def place_unit_curve_flat(face_record, u, v, unit_curve, size, rotation):
    """Orient a unit curve onto the local frame at normalized (u, v) without pullback.

    Draft-preview path: scales by size, rotates by rotation (radians) about the local
    normal, and returns the planar curve lying on the frame; None when the frame
    cannot be evaluated. Never calls PullToBrepFace.
    """
    frame = local_frame(face_record, u, v)
    if frame is None:
        return None
    curve = unit_curve.DuplicateCurve()
    scale = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, size)
    rotate = Rhino.Geometry.Transform.Rotation(
        rotation, Rhino.Geometry.Vector3d.ZAxis, Rhino.Geometry.Point3d.Origin
    )
    orient = Rhino.Geometry.Transform.PlaneToPlane(Rhino.Geometry.Plane.WorldXY, frame)
    curve.Transform(orient * rotate * scale)
    return curve


def place_unit_curve(face_record, u, v, unit_curve, size, rotation):
    """Orient a unit curve (XY plane, origin-centered) onto the face at normalized (u, v).

    Scales by size, rotates by rotation (radians) about the local normal, then pulls the
    curve onto the BrepFace. Returns (curve, pulled): pulled is False when pullback failed
    and the returned curve is the planar fallback lying on the local frame. (None, False)
    when the frame itself cannot be evaluated.
    """
    curve = place_unit_curve_flat(face_record, u, v, unit_curve, size, rotation)
    if curve is None:
        return None, False

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
