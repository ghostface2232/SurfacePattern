#! python3
# Singleton session state (scriptcontext.sticky) and recompute orchestration.

from dataclasses import dataclass, field

import Rhino
import scriptcontext

STICKY_KEY = "surfacepattern_session"


@dataclass
class FaceRecord:
    """One target face: source object id, face index, raw UV domain, underlying surface."""

    object_id: object            # System.Guid of the source document object
    face_index: int
    domain_u: tuple              # raw (t0, t1) of the face U interval
    domain_v: tuple              # raw (t0, t1) of the face V interval
    surface: object              # Rhino.Geometry.Surface (underlying, untrimmed)
    orientation_reversed: bool   # BrepFace.OrientationIsReversed at pick time

    def resolve_face(self):
        """Re-resolve the live BrepFace from the document; None if the object is gone."""
        rhobj = scriptcontext.doc.Objects.FindId(self.object_id)
        if rhobj is None:
            return None
        brep = Rhino.Geometry.Brep.TryConvertBrep(rhobj.Geometry)
        if brep is None or self.face_index >= brep.Faces.Count:
            return None
        return brep.Faces[self.face_index]


@dataclass
class PatternSession:
    """Singleton holding targets, pattern parameters, and the preview cache."""

    targets: list = field(default_factory=list)      # list of FaceRecord
    attractors: list = field(default_factory=list)   # document object ids (points/curves)
    params: dict = field(default_factory=dict)       # current pattern parameters
    stamps: list = field(default_factory=list)       # normalized (nurbs, draft) unit curve pairs
    manual_placements: list = field(default_factory=list)  # (FaceRecord, u, v, size_factor, rotation_offset, stamp_index)
    freehand_strokes: list = field(default_factory=list)   # independent pulled curves shown in stamp previews
    preview_curves: list = field(default_factory=list)        # full NURBS curves
    preview_records: list = field(default_factory=list)       # FaceRecord per full curve (None for strokes)
    preview_draft_curves: list = field(default_factory=list)  # polyline approximations
    preview_quality: str = "draft"                   # "draft" | "full"
    pullback_failures: int = 0                       # curves that fell back to planar on last full recompute

    def suggest_placement_mode(self):
        """Return the default placement mode for the current targets: 'uv' or 'world'."""
        return "uv" if len(self.targets) == 1 else "world"

    def prune_dead_targets(self):
        """Drop targets whose source object no longer resolves; return number removed."""
        alive = [record for record in self.targets if record.resolve_face() is not None]
        removed = len(self.targets) - len(alive)
        self.targets = alive
        return removed

    def resolve_attractors(self):
        """Live attractor geometry as (points, curves), silently pruning deleted ids.

        Geometry is re-fetched from the document on every call so moved attractors
        are always current.
        """
        points, curves, alive = [], [], []
        for object_id in self.attractors:
            rhobj = scriptcontext.doc.Objects.FindId(object_id)
            if rhobj is None:
                continue
            geometry = rhobj.Geometry
            if isinstance(geometry, Rhino.Geometry.Point):
                points.append(geometry.Location)
                alive.append(object_id)
            elif isinstance(geometry, Rhino.Geometry.Curve):
                curves.append(geometry)
                alive.append(object_id)
        self.attractors = alive
        return points, curves

    def clear_preview(self):
        """Empty both preview curve caches."""
        self.preview_curves = []
        self.preview_records = []
        self.preview_draft_curves = []

    def set_preview(self, curves, draft_curves):
        """Cache full curves and draft approximations, then request a redraw."""
        self.preview_curves = list(curves)
        self.preview_draft_curves = list(draft_curves)
        scriptcontext.doc.Views.Redraw()

    def set_preview_enabled(self, enabled):
        """Turn the preview conduit on/off — the ui layer's only path to the conduit."""
        from surfacepattern.preview.conduit import get_conduit

        if enabled:
            get_conduit().enable()
        else:
            get_conduit().disable()

    def request_recompute(self, draft):
        """Run the active pattern engine and rebuild the preview cache (draft skips pullback)."""
        # Local imports keep module load order safe (preview.conduit imports this module).
        from surfacepattern.core import mapping
        from surfacepattern.engine import grid, halftone, shapes, stamp
        from surfacepattern.preview.conduit import get_conduit

        self.preview_quality = "draft" if draft else "full"
        self.prune_dead_targets()
        if not self.targets:
            self.clear_preview()
            scriptcontext.doc.Views.Redraw()
            return

        # Placement jobs: (record, u, v, size, rotation, nurbs_unit, draft_unit).
        # Grid/halftone share one unit shape; stamp placements each carry a stamp index.
        mode = self.params.get("pattern_mode", "grid")
        extra_curves = []
        if mode == "stamp":
            jobs = []
            for record, u, v, size, rotation, index in stamp.generate(self):
                nurbs_unit, draft_unit = self.stamps[index % len(self.stamps)]
                jobs.append((record, u, v, size, rotation, nurbs_unit, draft_unit))
            # Freehand strokes are already pulled, document-independent curves.
            extra_curves = list(self.freehand_strokes)
        else:
            engines = {"grid": grid, "halftone": halftone}
            engine = engines.get(mode)
            if engine is None:
                return
            nurbs_unit, draft_unit = shapes.unit_shape(
                self.params.get("shape", "circle"), self.params.get("slot_ratio", 0.4)
            )
            jobs = [
                (record, u, v, size, rotation, nurbs_unit, draft_unit)
                for record, u, v, size, rotation in engine.generate(self)
            ]

        if draft:
            curves = []
            for record, u, v, size, rotation, _nurbs_unit, draft_unit in jobs:
                curve = mapping.place_unit_curve_flat(record, u, v, draft_unit, size, rotation)
                if curve is not None:
                    curves.append(curve)
            self.preview_draft_curves = curves + extra_curves
        else:
            self.pullback_failures = 0
            curves = []
            curve_records = []  # face association per curve, so bake/trim can group by face
            for record, u, v, size, rotation, nurbs_unit, _draft_unit in jobs:
                curve, pulled = mapping.place_unit_curve(record, u, v, nurbs_unit, size, rotation)
                if curve is not None:
                    curves.append(curve)
                    curve_records.append(record)
                    if not pulled:
                        self.pullback_failures += 1
            self.preview_curves = curves + extra_curves
            self.preview_records = curve_records + [None] * len(extra_curves)
            if self.pullback_failures:
                Rhino.RhinoApp.WriteLine(
                    "SurfacePattern: {} curves failed pullback (planar fallback used).".format(
                        self.pullback_failures
                    )
                )
        get_conduit().enable()  # enables when needed; always redraws


def get_session():
    """Return the singleton PatternSession stored in scriptcontext.sticky."""
    existing = scriptcontext.sticky.get(STICKY_KEY)
    # Name-based check keeps the session alive across ScriptEditor module reloads,
    # where the class object identity changes and isinstance() would fail.
    if existing is not None and type(existing).__name__ == "PatternSession":
        if type(existing) is PatternSession:
            return existing
        # Instance from before a module reload: rebind its state onto the current
        # class so fields and methods added since (e.g. stamp state) exist.
        migrated = PatternSession()
        for name in migrated.__dataclass_fields__:
            setattr(migrated, name, getattr(existing, name, getattr(migrated, name)))
        scriptcontext.sticky[STICKY_KEY] = migrated
        return migrated
    fresh = PatternSession()
    scriptcontext.sticky[STICKY_KEY] = fresh
    return fresh


def _make_face_record(object_id, face):
    """Build a FaceRecord snapshot from a live BrepFace."""
    interval_u = face.Domain(0)
    interval_v = face.Domain(1)
    return FaceRecord(
        object_id=object_id,
        face_index=face.FaceIndex,
        domain_u=(interval_u.T0, interval_u.T1),
        domain_v=(interval_v.T0, interval_v.T1),
        surface=face.UnderlyingSurface(),
        orientation_reversed=face.OrientationIsReversed,
    )


def pick_targets(session=None):
    """Interactively pick surfaces/polysurfaces (sub-faces allowed) into the session; True on success."""
    session = session if session is not None else get_session()

    getter = Rhino.Input.Custom.GetObject()
    getter.SetCommandPrompt("Select target surfaces or polysurfaces")
    getter.GeometryFilter = (
        Rhino.DocObjects.ObjectType.Surface | Rhino.DocObjects.ObjectType.PolysrfFilter
    )
    getter.SubObjectSelect = True
    getter.GroupSelect = False
    getter.GetMultiple(1, 0)
    if getter.CommandResult() != Rhino.Commands.Result.Success:
        return False

    records = []
    seen = set()
    for objref in getter.Objects():
        brep = objref.Brep()
        if brep is None:
            continue
        picked_face = objref.Face()  # non-None when a sub-object face was picked
        faces = [picked_face] if picked_face is not None else list(brep.Faces)
        for face in faces:
            key = (objref.ObjectId, face.FaceIndex)
            if key in seen:
                continue
            seen.add(key)
            records.append(_make_face_record(objref.ObjectId, face))

    if not records:
        return False

    session.targets = records
    session.clear_preview()
    session.params["placement_mode"] = session.suggest_placement_mode()
    return True


def pick_attractors(session=None):
    """Interactively pick document points/curves as attractors; True on success."""
    session = session if session is not None else get_session()

    getter = Rhino.Input.Custom.GetObject()
    getter.SetCommandPrompt("Select attractor points or curves")
    getter.GeometryFilter = (
        Rhino.DocObjects.ObjectType.Point | Rhino.DocObjects.ObjectType.Curve
    )
    getter.SubObjectSelect = False
    getter.GroupSelect = False
    getter.GetMultiple(1, 0)
    if getter.CommandResult() != Rhino.Commands.Result.Success:
        return False

    session.attractors = [objref.ObjectId for objref in getter.Objects()]
    return True


# Stamp entry points: thin wrappers so the ui layer keeps talking only to the
# session while the interactive logic lives with the stamp engine.


def pick_stamps(session=None):
    """Interactively register closed planar curves as stamps; True on success."""
    from surfacepattern.engine import stamp

    session = session if session is not None else get_session()
    return stamp.register_stamps(session)


def stamp_click_place(session=None):
    """Run the stamp click-to-place loop; returns the number of stamps placed."""
    from surfacepattern.engine import stamp

    session = session if session is not None else get_session()
    return stamp.click_place(session)


def stamp_draw_freehand(session=None):
    """Run the freehand stroke loop; True when a stroke was added."""
    from surfacepattern.engine import stamp

    session = session if session is not None else get_session()
    return stamp.draw_freehand(session)


def clear_stamp_placements(session=None):
    """Drop all manual stamp placements and freehand strokes; returns how many were removed."""
    session = session if session is not None else get_session()
    removed = len(session.manual_placements) + len(session.freehand_strokes)
    session.manual_placements = []
    session.freehand_strokes = []
    session.request_recompute(False)
    return removed


# Bake entry points: thin wrappers so the ui layer keeps talking only to the
# session while core.bake stays the sole document writer.


def bake_curves(session=None):
    """Bake the full preview curves to the document; returns the number added."""
    from surfacepattern.core import bake

    session = session if session is not None else get_session()
    return bake.bake_curves(session)


def bake_with_trim(session=None):
    """Bake curves then trim the target faces; returns the number of holes cut."""
    from surfacepattern.core import bake

    session = session if session is not None else get_session()
    return bake.bake_with_trim(session)
