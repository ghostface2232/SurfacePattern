#! python3
# Sole document writer: bake preview curves to layers and the optional trim pipeline.
#
# Layer scheme: everything lands under a "SurfacePattern" parent layer — baked
# curves on per-operation sublayers (grid_01, halftone_02, ...), preserved
# originals on a hidden "originals" sublayer. Every baked curve carries the
# generating parameters as a JSON UserString for traceability.

import json

import System

import Rhino
import scriptcontext

PARENT_LAYER = "SurfacePattern"
ORIGINALS_LAYER = "originals"
PARAMS_USERSTRING_KEY = "surfacepattern_params"
TRIM_WARN_THRESHOLD = 300   # holes; above this a confirmation dialog runs first


# ---- layers -----------------------------------------------------------------


def _ensure_parent_layer():
    """Return the SurfacePattern root layer, creating it when missing."""
    doc = scriptcontext.doc
    index = doc.Layers.FindByFullPath(PARENT_LAYER, -1)
    if index >= 0:
        return doc.Layers[index]
    layer = Rhino.DocObjects.Layer()
    layer.Name = PARENT_LAYER
    return doc.Layers[doc.Layers.Add(layer)]


def _ensure_child_layer(parent_layer, name, visible=True):
    """Return the named sublayer under the parent, creating it when missing."""
    doc = scriptcontext.doc
    index = doc.Layers.FindByFullPath(parent_layer.Name + "::" + name, -1)
    if index >= 0:
        return doc.Layers[index]
    layer = Rhino.DocObjects.Layer()
    layer.Name = name
    layer.ParentLayerId = parent_layer.Id
    layer.IsVisible = visible
    return doc.Layers[doc.Layers.Add(layer)]


def _next_operation_name(parent_layer, mode):
    """First unused '<mode>_<nn>' sublayer name under the parent."""
    highest = 0
    prefix = mode + "_"
    for layer in scriptcontext.doc.Layers:
        if layer.IsDeleted or layer.ParentLayerId != parent_layer.Id:
            continue
        if not layer.Name.startswith(prefix):
            continue
        suffix = layer.Name[len(prefix):]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return "{}_{:02d}".format(mode, highest + 1)


# ---- curve bake -------------------------------------------------------------


def _serializable_params(params):
    """Copy of params with JSON-unserializable values (e.g. projection_plane) dropped."""
    out = {}
    for key, value in params.items():
        try:
            json.dumps(value)
        except TypeError:
            continue
        out[key] = value
    return out


def _ensure_full_preview(session):
    """Make sure the preview cache holds full-quality curves for the current params."""
    if session.preview_quality != "full" or not session.preview_curves:
        session.request_recompute(False)


def _bake_curves_core(session):
    """Add the full preview curves to a fresh operation sublayer (no undo handling).

    Returns ([(object_id, face_record_or_None, curve), ...], operation_name), or
    (None, None) when there is nothing to bake.
    """
    _ensure_full_preview(session)
    curves = session.preview_curves
    records = session.preview_records
    if not curves:
        Rhino.RhinoApp.WriteLine("SurfacePattern: nothing to bake — preview is empty.")
        return None, None
    if len(records) != len(curves):
        records = [None] * len(curves)

    doc = scriptcontext.doc
    parent = _ensure_parent_layer()
    operation_name = _next_operation_name(parent, session.params.get("pattern_mode", "grid"))
    layer = _ensure_child_layer(parent, operation_name)
    params_json = json.dumps(_serializable_params(session.params), sort_keys=True)

    baked = []
    for curve, record in zip(curves, records):
        attributes = doc.CreateDefaultAttributes()
        attributes.LayerIndex = layer.Index
        attributes.SetUserString(PARAMS_USERSTRING_KEY, params_json)
        object_id = doc.Objects.AddCurve(curve, attributes)
        if object_id != System.Guid.Empty:
            baked.append((object_id, record, curve))
    return baked, operation_name


def bake_curves(session):
    """Bake the full-quality preview curves onto a fresh SurfacePattern sublayer.

    Each curve carries the generating parameters as a JSON UserString; the whole
    bake is one undo record. Returns the number of curves added.
    """
    doc = scriptcontext.doc
    undo_serial = doc.BeginUndoRecord("SurfacePattern Bake Curves")
    try:
        baked, operation_name = _bake_curves_core(session)
    finally:
        doc.EndUndoRecord(undo_serial)
    if baked is None:
        return 0
    doc.Views.Redraw()
    Rhino.RhinoApp.WriteLine(
        "SurfacePattern: baked {} curve(s) to {}::{}.".format(
            len(baked), PARENT_LAYER, operation_name
        )
    )
    return len(baked)


# ---- trim pipeline ----------------------------------------------------------


def _curve_centroid(curve):
    """Area centroid of a closed curve (bounding-box center fallback for odd cases)."""
    mass = Rhino.Geometry.AreaMassProperties.Compute(curve)
    if mass is not None:
        return mass.Centroid
    return curve.GetBoundingBox(True).Center


def _piece_containing(pieces, point):
    """Index of the single-face brep piece lying closest to the point (trim-aware)."""
    best = None
    for index, piece in enumerate(pieces):
        face = piece.Faces[0]
        ok, u, v = face.ClosestPoint(point)
        if not ok:
            continue
        if face.IsPointOnFace(u, v) == Rhino.Geometry.PointFaceRelation.Exterior:
            continue
        distance = point.DistanceTo(face.PointAt(u, v))
        if best is None or distance < best[0]:
            best = (distance, index)
    return best[1] if best is not None else None


def _split_piece(piece, curve, tolerance):
    """Split a single-face brep piece with one closed curve; list of pieces or None.

    Retries at half document tolerance before giving up on the curve.
    """
    face = piece.Faces[0]
    result = face.Split([curve], tolerance)
    if result is None or result.Faces.Count <= 1:
        result = face.Split([curve], tolerance * 0.5)
    if result is None or result.Faces.Count <= 1:
        return None
    return [result.Faces[i].DuplicateFace(False) for i in range(result.Faces.Count)]


def _trim_object(source_id, face_map, tolerance, originals_layer, cancelled, failed_ids, progress):
    """Split one object's target faces and replace it with the perforated join.

    face_map: {face_index: [(baked_curve_id, curve), ...]} with closed curves only.
    Returns the number of holes cut, or None when the object was skipped or the
    user cancelled — in that case this object's document state is untouched.
    """
    doc = scriptcontext.doc
    rhobj = doc.Objects.FindId(source_id)
    if rhobj is None:
        return None
    brep = Rhino.Geometry.Brep.TryConvertBrep(rhobj.Geometry)
    if brep is None:
        return None

    kept_pieces = []
    holes = 0
    for face in brep.Faces:
        curve_items = face_map.get(face.FaceIndex)
        if not curve_items:
            kept_pieces.append(face.DuplicateFace(False))
            continue
        pieces = [face.DuplicateFace(False)]
        centroids = []
        for curve_id, curve in curve_items:
            Rhino.RhinoApp.Wait()  # pump the message loop so Esc registers mid-trim
            if cancelled["flag"]:
                return None
            progress["count"] += 1
            Rhino.UI.StatusBar.UpdateProgressMeter(progress["count"], True)
            anchor = curve.PointAt(curve.Domain.Mid)
            index = _piece_containing(pieces, anchor)
            if index is None:
                failed_ids.append(curve_id)
                continue
            split = _split_piece(pieces[index], curve, tolerance)
            if split is None:
                failed_ids.append(curve_id)
                continue
            pieces[index:index + 1] = split
            centroids.append(_curve_centroid(curve))
        # Hole interiors: the piece each curve's area centroid lands on.
        for centroid in centroids:
            index = _piece_containing(pieces, centroid)
            if index is not None and len(pieces) > 1:
                pieces.pop(index)
                holes += 1
        kept_pieces.extend(pieces)

    if cancelled["flag"] or not kept_pieces:
        return None
    if originals_layer is not None:
        attributes = rhobj.Attributes.Duplicate()
        attributes.LayerIndex = originals_layer.Index
        doc.Objects.Add(rhobj.Geometry.Duplicate(), attributes)
    joined = Rhino.Geometry.Brep.JoinBreps(kept_pieces, tolerance * 2.0)
    if joined is None or len(joined) == 0:
        joined = kept_pieces
    attributes = rhobj.Attributes.Duplicate()
    doc.Objects.Delete(rhobj, True)
    for piece in joined:
        doc.Objects.AddBrep(piece, attributes)
    return holes


def bake_with_trim(session):
    """Bake curves, then perforate the target faces with the closed baked curves.

    Per face: split with each closed curve, delete the piece containing the curve's
    area centroid, join the rest, and replace the source object (optionally
    preserving a copy on a hidden layer, per bake_keep_original). Open curves
    (freehand strokes) are excluded from trimming and reported. Esc cancels and
    undoes everything done so far; curves that fail to split are skipped and left
    selected. Returns the number of holes cut.
    """
    _ensure_full_preview(session)
    curves = session.preview_curves
    records = session.preview_records
    if len(records) != len(curves):
        records = [None] * len(curves)
    closed_count = sum(
        1 for curve, record in zip(curves, records) if record is not None and curve.IsClosed
    )
    if closed_count == 0:
        Rhino.RhinoApp.WriteLine("SurfacePattern: no closed curves to trim — baking curves only.")
        bake_curves(session)
        return 0
    if closed_count > TRIM_WARN_THRESHOLD:
        answer = Rhino.UI.Dialogs.ShowMessage(
            "{} holes will be trimmed — this can take several minutes.\n"
            "Esc cancels and undoes the operation. Continue?".format(closed_count),
            "SurfacePattern Trim",
            Rhino.UI.ShowMessageButton.YesNo,
            Rhino.UI.ShowMessageIcon.Warning,
        )
        if answer != Rhino.UI.ShowMessageResult.Yes:
            Rhino.RhinoApp.WriteLine("SurfacePattern: trim cancelled before start.")
            return 0

    doc = scriptcontext.doc
    tolerance = doc.ModelAbsoluteTolerance
    cancelled = {"flag": False}

    def _on_escape(_sender, _event):
        cancelled["flag"] = True

    aborted = False
    failed_ids = []
    skipped_open = 0
    holes_cut = 0
    objects_trimmed = 0
    progress = {"count": 0}

    undo_serial = doc.BeginUndoRecord("SurfacePattern Bake + Trim")
    Rhino.RhinoApp.EscapeKeyPressed += _on_escape
    Rhino.UI.StatusBar.ShowProgressMeter(0, closed_count, "SurfacePattern trim", True, True)
    try:
        baked, _operation_name = _bake_curves_core(session)
        if baked is None:
            return 0

        by_object = {}  # source object id -> {face_index: [(baked_curve_id, curve), ...]}
        for object_id, record, curve in baked:
            if record is None or not curve.IsClosed:
                skipped_open += 1
                continue
            by_object.setdefault(record.object_id, {}).setdefault(
                record.face_index, []
            ).append((object_id, curve))

        originals_layer = None
        if bool(session.params.get("bake_keep_original", True)):
            originals_layer = _ensure_child_layer(
                _ensure_parent_layer(), ORIGINALS_LAYER, visible=False
            )

        for source_id, face_map in by_object.items():
            if cancelled["flag"]:
                aborted = True
                break
            outcome = _trim_object(
                source_id, face_map, tolerance, originals_layer, cancelled, failed_ids, progress
            )
            if cancelled["flag"]:
                aborted = True
                break
            if outcome is not None:
                holes_cut += outcome
                objects_trimmed += 1
    finally:
        Rhino.UI.StatusBar.HideProgressMeter()
        Rhino.RhinoApp.EscapeKeyPressed -= _on_escape
        doc.EndUndoRecord(undo_serial)

    if aborted:
        doc.Undo()
        doc.Views.Redraw()
        Rhino.RhinoApp.WriteLine("SurfacePattern: trim cancelled — changes undone.")
        return 0

    # Source objects were replaced: recompute prunes dead targets and clears the preview.
    session.request_recompute(False)
    if failed_ids:
        doc.Objects.UnselectAll()
        for failed_id in failed_ids:
            doc.Objects.Select(failed_id)
    doc.Views.Redraw()
    message = "SurfacePattern: trimmed {} hole(s) across {} object(s)".format(
        holes_cut, objects_trimmed
    )
    if skipped_open:
        message += "; {} open curve(s) excluded from trim".format(skipped_open)
    if failed_ids:
        message += "; {} curve(s) failed to split (selected)".format(len(failed_ids))
    Rhino.RhinoApp.WriteLine(message + ".")
    return holes_cut
