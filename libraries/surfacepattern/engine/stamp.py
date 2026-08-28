#! python3
# Custom stamp engine: array, click-place, and freehand placements.
#
# Layering note: generate() is pure parameter space like the other engines. The
# interactive entry points (register_stamps, click_place, draw_freehand) read the
# document for picking, mirroring core.session's pick_* functions — ui reaches
# them only through core.session wrappers, and nothing here ever writes document
# objects. All surface evaluation goes through core.mapping.

import math
import random

import System.Drawing

import Rhino
import scriptcontext

from surfacepattern.core import mapping
from surfacepattern.engine import grid

DEFAULTS = {
    "stamp_size": 1.0,            # mm; bounding size of a placed stamp
    "stamp_rotation": 0.0,        # degrees, applied to every placement
    "stamp_jitter": 0.0,          # 0-100 (%); size and rotation jitter
    "stamp_select": "cycle",      # "cycle" | "random" among registered stamps
    "stamp_place_mode": "array",  # "array" | "click" | "freehand"
}

DRAFT_SEGMENTS = 24               # registered curves are arbitrary, so more than primitive shapes
GHOST_COLOR = System.Drawing.Color.FromArgb(255, 255, 200, 100)
GHOST_THICKNESS = 2


def param(session, key):
    """Session parameter with engine default fallback."""
    return session.params.get(key, DEFAULTS.get(key))


# ---- registration -----------------------------------------------------------


def normalize_stamp(curve, tolerance):
    """Normalize a closed planar curve to unit bounds on WorldXY at the origin.

    Returns ((nurbs, draft_polyline), None) on success, (None, reason) otherwise.
    The copy is centered on the origin and scaled so its larger bounding dimension
    is 1, so the size parameter scales it directly like the primitive unit shapes.
    """
    if curve is None or not curve.IsClosed:
        return None, "not a closed curve"
    ok, plane = curve.TryGetPlane(tolerance)
    if not ok:
        return None, "not planar"
    nurbs = curve.ToNurbsCurve()
    if nurbs is None:
        return None, "no NURBS form"
    nurbs = nurbs.DuplicateCurve()  # never transform geometry owned by the document
    nurbs.Transform(
        Rhino.Geometry.Transform.PlaneToPlane(plane, Rhino.Geometry.Plane.WorldXY)
    )
    bbox = nurbs.GetBoundingBox(True)
    extent = max(bbox.Max.X - bbox.Min.X, bbox.Max.Y - bbox.Min.Y)
    if not bbox.IsValid or extent <= tolerance:
        return None, "degenerate size"
    center = bbox.Center
    move = Rhino.Geometry.Transform.Translation(-center.X, -center.Y, -center.Z)
    scale = Rhino.Geometry.Transform.Scale(Rhino.Geometry.Point3d.Origin, 1.0 / extent)
    nurbs.Transform(scale * move)
    return (nurbs, _draft_polyline(nurbs)), None


def _draft_polyline(curve):
    """Closed polyline approximation of a unit stamp for draft preview."""
    parameters = curve.DivideByCount(DRAFT_SEGMENTS, True)
    if parameters is None or len(parameters) < 3:
        return curve.DuplicateCurve()
    points = [curve.PointAt(t) for t in parameters]
    if points[0].DistanceTo(points[-1]) > 1e-9:
        points.append(points[0])
    return Rhino.Geometry.PolylineCurve(points)


def register_stamps(session):
    """Pick closed planar document curves and register normalized unit copies; True on success.

    Non-planar (or otherwise unusable) picks are rejected with a command-line notice;
    the remaining valid curves replace the session's stamp list.
    """
    getter = Rhino.Input.Custom.GetObject()
    getter.SetCommandPrompt("Select closed planar curves to register as stamps")
    getter.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
    getter.GeometryAttributeFilter = Rhino.Input.Custom.GeometryAttributeFilter.ClosedCurve
    getter.SubObjectSelect = False
    getter.GroupSelect = False
    getter.GetMultiple(1, 0)
    if getter.CommandResult() != Rhino.Commands.Result.Success:
        return False

    tolerance = scriptcontext.doc.ModelAbsoluteTolerance
    stamps, rejected = [], []
    for objref in getter.Objects():
        pair, reason = normalize_stamp(objref.Curve(), tolerance)
        if pair is not None:
            stamps.append(pair)
        else:
            rejected.append(reason)
    if rejected:
        Rhino.RhinoApp.WriteLine(
            "SurfacePattern: {} curve(s) rejected ({}) — stamps must be closed planar curves.".format(
                len(rejected), ", ".join(sorted(set(rejected)))
            )
        )
    if not stamps:
        return False
    session.stamps = stamps
    return True


# ---- generation -------------------------------------------------------------


def generate(session):
    """Return [(face_record, u, v, size_mm, rotation_radians, stamp_index), ...].

    Manual (click-placed) items always render; the array lattice is added on top
    only in array placement mode. Freehand strokes are independent curves owned by
    the session's preview, not placements.
    """
    if not session.stamps:
        return []
    placements = _manual_placements(session)
    if param(session, "stamp_place_mode") == "array":
        placements.extend(_array_placements(session))
    return placements


def _jittered_attributes(session, rng):
    """One placement's (size, rotation_radians) from stamp params with jitter applied."""
    size = float(param(session, "stamp_size"))
    rotation = math.radians(float(param(session, "stamp_rotation")))
    jitter = float(param(session, "stamp_jitter")) / 100.0
    if jitter > 0.0:
        size *= 1.0 + jitter * rng.uniform(-0.5, 0.5)
        rotation += jitter * rng.uniform(-math.pi, math.pi)
    return size, rotation


def _pick_index(session, rng, ordinal):
    """Stamp index for the ordinal-th placement per the cycle/random selection option."""
    count = len(session.stamps)
    if count <= 1:
        return 0
    if param(session, "stamp_select") == "random":
        return rng.randrange(count)
    return ordinal % count


def _array_placements(session):
    """Array mode: the grid engine supplies the lattice; size/rotation/index are stamp-driven."""
    rng = random.Random(int(grid.param(session, "seed")))
    out = []
    for ordinal, (record, u, v, _size, _rotation) in enumerate(grid.generate(session)):
        size, rotation = _jittered_attributes(session, rng)
        out.append((record, u, v, size, rotation, _pick_index(session, rng, ordinal)))
    return out


def _manual_placements(session):
    """Manual placements with the current global size/rotation applied; prunes dead faces.

    Stored items are (face_record, u, v, size_factor, rotation_offset, stamp_index):
    factors relative to the stamp sliders, so Size/Rotation bulk-adjust everything placed.
    """
    size_base = float(param(session, "stamp_size"))
    rotation_base = math.radians(float(param(session, "stamp_rotation")))
    count = len(session.stamps)
    alive, out = [], []
    for item in session.manual_placements:
        record, u, v, size_factor, rotation_offset, index = item
        if record.resolve_face() is None:
            continue
        alive.append(item)
        out.append(
            (record, u, v, size_base * size_factor, rotation_base + rotation_offset, index % count)
        )
    session.manual_placements = alive
    return out


# ---- interactive placement --------------------------------------------------


def _target_constraint(session):
    """(brep, face_index) to constrain GetPoint to, or None without resolvable targets.

    GetPoint can constrain to a single Brep only: with targets from several objects
    the first object's Brep is used and a notice is printed.
    """
    records = [record for record in session.targets if record.resolve_face() is not None]
    if not records:
        return None
    first = records[0]
    same_object = [record for record in records if record.object_id == first.object_id]
    if len(same_object) < len(records):
        Rhino.RhinoApp.WriteLine(
            "SurfacePattern: multiple target objects — clicks are constrained to the first one."
        )
    face = first.resolve_face()
    face_index = first.face_index if len(same_object) == 1 else -1
    return face.Brep, face_index


def _snap_threshold():
    """Max distance for accepting a picked point as lying on a target face."""
    return max(scriptcontext.doc.ModelAbsoluteTolerance * 10.0, 1e-6)


def _ghost_handler(session, draft_unit, size, rotation, threshold):
    """DynamicDraw handler: ghost of the pending stamp following the mouse on the faces."""
    def handler(_sender, e):
        try:
            hit = mapping.closest_face_uv(session.targets, e.CurrentPoint, threshold)
            if hit is None:
                return
            record, u, v = hit
            curve = mapping.place_unit_curve_flat(record, u, v, draft_unit, size, rotation)
            if curve is not None:
                e.Display.DrawCurve(curve, GHOST_COLOR, GHOST_THICKNESS)
        except Exception:
            pass  # a per-frame draw handler must never raise into Rhino's display loop
    return handler


def click_place(session):
    """Click-to-place loop on the target faces; returns the number of stamps placed.

    Each click stores a manual placement (bulk-adjustable later via the stamp
    sliders) and refreshes the draft preview. Enter/right-click finishes; Esc rolls
    back only the placements added during this run.
    """
    if not session.stamps:
        Rhino.RhinoApp.WriteLine("SurfacePattern: register stamps first.")
        return 0
    constraint = _target_constraint(session)
    if constraint is None:
        Rhino.RhinoApp.WriteLine("SurfacePattern: pick targets first.")
        return 0
    brep, face_index = constraint
    threshold = _snap_threshold()
    # Seed offset by the existing placement count keeps repeated runs reproducible
    # without replaying the same jitter sequence every time.
    rng = random.Random(int(grid.param(session, "seed")) + len(session.manual_placements))
    size_base = max(float(param(session, "stamp_size")), 1e-9)
    rotation_base = math.radians(float(param(session, "stamp_rotation")))

    added = []
    while True:
        size, rotation = _jittered_attributes(session, rng)
        index = _pick_index(session, rng, len(session.manual_placements))
        _nurbs_unit, draft_unit = session.stamps[index]

        getter = Rhino.Input.Custom.GetPoint()
        getter.SetCommandPrompt(
            "Click on a target face to place a stamp (Enter to finish, Esc to cancel)"
        )
        getter.AcceptNothing(True)
        getter.Constrain(brep, -1, face_index, False)
        getter.DynamicDraw += _ghost_handler(session, draft_unit, size, rotation, threshold)
        result = getter.Get()

        if result == Rhino.Input.GetResult.Point:
            hit = mapping.closest_face_uv(session.targets, getter.Point(), threshold)
            if hit is None:
                Rhino.RhinoApp.WriteLine("SurfacePattern: click missed the target faces.")
                continue
            record, u, v = hit
            item = (record, u, v, size / size_base, rotation - rotation_base, index)
            session.manual_placements.append(item)
            added.append(item)
            session.request_recompute(True)  # placed stamps show immediately (draft)
        elif result == Rhino.Input.GetResult.Nothing:
            break  # Enter / right-click accepts what was placed
        else:
            # Esc or failure: roll back only this run's placements (identity-based —
            # value equality could match an older, identical placement).
            added_ids = set(map(id, added))
            session.manual_placements = [
                item for item in session.manual_placements if id(item) not in added_ids
            ]
            added = []
            break
    session.request_recompute(False)
    return len(added)


def _stroke_handler(points):
    """DynamicDraw handler: the stroke so far plus a rubber band to the mouse."""
    def handler(_sender, e):
        try:
            chain = list(points) + [e.CurrentPoint]
            for start, end in zip(chain, chain[1:]):
                e.Display.DrawLine(start, end, GHOST_COLOR, GHOST_THICKNESS)
        except Exception:
            pass  # a per-frame draw handler must never raise into Rhino's display loop
    return handler


def draw_freehand(session):
    """Click points on a target face, interpolate them, and pull the stroke onto the face.

    The finished stroke is an independent preview curve unrelated to the registered
    stamps. Enter/right-click finishes; Esc cancels the in-progress stroke (nothing
    is committed until the stroke completes). True when a stroke was added.
    """
    constraint = _target_constraint(session)
    if constraint is None:
        Rhino.RhinoApp.WriteLine("SurfacePattern: pick targets first.")
        return False
    brep, face_index = constraint
    threshold = _snap_threshold()

    points = []
    while True:
        getter = Rhino.Input.Custom.GetPoint()
        getter.SetCommandPrompt(
            "Click stroke points on the target face (Enter to finish, Esc to cancel)"
        )
        getter.AcceptNothing(True)
        getter.Constrain(brep, -1, face_index, False)
        getter.DynamicDraw += _stroke_handler(points)
        result = getter.Get()
        if result == Rhino.Input.GetResult.Point:
            points.append(getter.Point())
        elif result == Rhino.Input.GetResult.Nothing:
            break
        else:
            return False  # Esc: the uncommitted stroke is simply discarded

    if len(points) < 2:
        if points:
            Rhino.RhinoApp.WriteLine("SurfacePattern: a stroke needs at least 2 points.")
        return False
    curve = Rhino.Geometry.Curve.CreateInterpolatedCurve(points, 3)
    if curve is None:
        Rhino.RhinoApp.WriteLine("SurfacePattern: stroke interpolation failed.")
        return False

    hit = mapping.closest_face_uv(session.targets, points[0], threshold)
    pulled = False
    if hit is not None:
        curve, pulled = mapping.pull_curve_to_face(hit[0], curve)
    if not pulled:
        Rhino.RhinoApp.WriteLine(
            "SurfacePattern: stroke pullback failed — interpolated curve kept."
        )
    session.freehand_strokes.append(curve)
    session.request_recompute(False)
    return True
