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
    params: dict = field(default_factory=dict)       # current pattern parameters
    preview_curves: list = field(default_factory=list)
    preview_quality: str = "draft"                   # "draft" | "full"

    def suggest_placement_mode(self):
        """Return the default placement mode for the current targets: 'uv' or 'world'."""
        return "uv" if len(self.targets) == 1 else "world"

    def prune_dead_targets(self):
        """Drop targets whose source object no longer resolves; return number removed."""
        alive = [record for record in self.targets if record.resolve_face() is not None]
        removed = len(self.targets) - len(alive)
        self.targets = alive
        return removed

    def clear_preview(self):
        """Empty the preview curve cache."""
        self.preview_curves = []


def get_session():
    """Return the singleton PatternSession stored in scriptcontext.sticky."""
    existing = scriptcontext.sticky.get(STICKY_KEY)
    # Name-based check keeps the session alive across ScriptEditor module reloads,
    # where the class object identity changes and isinstance() would fail.
    if existing is not None and type(existing).__name__ == "PatternSession":
        return existing
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
