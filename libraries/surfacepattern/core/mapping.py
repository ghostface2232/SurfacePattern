#! python3
# UV<->3D conversion, surface metrics, frames, distortion compensation, and curve pullback.

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


class SurfaceMetricSampler:
    """Live-face sampler for area-weighted, direction-independent placement."""

    def __init__(self, face_record):
        self.face_record = face_record
        self.face = face_record.resolve_face()
        self.surface = self.face.UnderlyingSurface() if self.face is not None else None

    def sample(self, u, v):
        """Return (3D point, normalized-UV area scale) or None outside the trimmed face."""
        if self.face is None or self.surface is None:
            return None
        su, sv = _denormalize(self.face_record, u, v)
        if self.face.IsPointOnFace(su, sv) == Rhino.Geometry.PointFaceRelation.Exterior:
            return None
        ok, point, derivatives = self.surface.Evaluate(su, sv, 1)
        if not ok or derivatives is None or len(derivatives) < 2:
            return None
        cross = Rhino.Geometry.Vector3d.CrossProduct(derivatives[0], derivatives[1])
        span_u = abs(self.face_record.domain_u[1] - self.face_record.domain_u[0])
        span_v = abs(self.face_record.domain_v[1] - self.face_record.domain_v[0])
        area_scale = cross.Length * span_u * span_v
        if area_scale <= 1e-12:
            return None
        return point, area_scale

    def estimate_area(self, divisions=12):
        """Return (trimmed area estimate, maximum sampled normalized-UV area scale)."""
        divisions = max(int(divisions), 2)
        area_sum = 0.0
        max_scale = 0.0
        for i in range(divisions):
            u = (i + 0.5) / divisions
            for j in range(divisions):
                sample = self.sample(u, (j + 0.5) / divisions)
                if sample is None:
                    continue
                _point, area_scale = sample
                area_sum += area_scale
                max_scale = max(max_scale, area_scale)
        return area_sum / (divisions * divisions), max_scale


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


def _closest_on_face(resolved, point):
    """Closest trimmed-region hit over (record, face) pairs: (distance, record, su, sv) or None."""
    best = None
    for record, face in resolved:
        ok, su, sv = face.ClosestPoint(point)
        if not ok:
            continue
        if face.IsPointOnFace(su, sv) == Rhino.Geometry.PointFaceRelation.Exterior:
            continue
        distance = point.DistanceTo(record.surface.PointAt(su, sv))
        if best is None or distance < best[0]:
            best = (distance, record, su, sv)
    return best


def closest_face_uv(face_records, point, max_distance=None):
    """Closest on-face hit to a 3D point: (face_record, u, v) normalized; None when out of range."""
    resolved, bbox = _resolve_with_bbox(face_records)
    if not resolved or not bbox.IsValid:
        return None
    if max_distance is None:
        max_distance = max(
            bbox.Diagonal.Length * 0.5, scriptcontext.doc.ModelAbsoluteTolerance
        )
    best = _closest_on_face(resolved, point)
    if best is None or best[0] > max_distance:
        return None
    _distance, record, su, sv = best
    u, v = _normalize(record, su, sv)
    return record, u, v


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


def pull_curve_to_face(face_record, curve):
    """Pull a 3D curve onto the face; (curve, pulled) with the input kept when pullback fails."""
    face = face_record.resolve_face()
    if face is None:
        return curve, False
    tolerance = scriptcontext.doc.ModelAbsoluteTolerance
    pulled = curve.PullToBrepFace(face, tolerance)
    if pulled is None or len(pulled) == 0:
        return curve, False
    if len(pulled) == 1:
        return pulled[0], True
    joined = Rhino.Geometry.Curve.JoinCurves(pulled, tolerance)
    if joined is not None and len(joined) > 0:
        return joined[0], True
    return pulled[0], True


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
    return pull_curve_to_face(face_record, curve)
