#! python3
# DisplayConduit for draft/full preview rendering (never writes to the document).

import System.Drawing

import Rhino
import scriptcontext

from surfacepattern.core.session import get_session

CONDUIT_STICKY_KEY = "surfacepattern_preview_conduit"
DEFAULT_DRAFT_CAP = 1500
PREVIEW_COLOR = System.Drawing.Color.FromArgb(255, 255, 128, 0)
PREVIEW_THICKNESS = 2
OVERFLOW_TEXT_HEIGHT = 14


class PatternPreviewConduit(Rhino.Display.DisplayConduit):
    """Draws the session's preview curve cache: draft polylines (capped) or full NURBS."""

    def _active_curves(self):
        """Return (curves_to_draw, total_count, is_draft) for the current session state."""
        session = get_session()
        if session.preview_quality == "draft":
            curves = session.preview_draft_curves
            cap = session.params.get("draft_cap", DEFAULT_DRAFT_CAP)
            total = len(curves)
            if total > cap:
                # Even subsampling down to the cap.
                curves = [curves[(i * total) // cap] for i in range(cap)]
            return curves, total, True
        return session.preview_curves, len(session.preview_curves), False

    def CalculateBoundingBox(self, e):
        curves, _total, _draft = self._active_curves()
        bbox = Rhino.Geometry.BoundingBox.Empty
        for curve in curves:
            bbox.Union(curve.GetBoundingBox(False))
        if bbox.IsValid:
            e.IncludeBoundingBox(bbox)

    def CalculateBoundingBoxZoomExtents(self, e):
        # Same union so preview participates in Zoom Extents.
        self.CalculateBoundingBox(e)

    def PostDrawObjects(self, e):
        curves, total, is_draft = self._active_curves()
        for curve in curves:
            e.Display.DrawCurve(curve, PREVIEW_COLOR, PREVIEW_THICKNESS)
        if is_draft and total > len(curves):
            bounds = e.Viewport.Bounds
            anchor = Rhino.Geometry.Point2d(bounds.Left + 12, bounds.Bottom - 16)
            e.Display.Draw2dText(
                "SurfacePattern preview: {} of {} shown".format(len(curves), total),
                PREVIEW_COLOR,
                anchor,
                False,
                OVERFLOW_TEXT_HEIGHT,
            )

    def enable(self):
        """Turn the conduit on and redraw."""
        if not self.Enabled:
            self.Enabled = True
        scriptcontext.doc.Views.Redraw()

    def disable(self):
        """Turn the conduit off and redraw."""
        if self.Enabled:
            self.Enabled = False
        scriptcontext.doc.Views.Redraw()


def get_conduit():
    """Return the singleton conduit stored in scriptcontext.sticky (survives command re-entry)."""
    existing = scriptcontext.sticky.get(CONDUIT_STICKY_KEY)
    if existing is not None and type(existing).__name__ == "PatternPreviewConduit":
        return existing
    fresh = PatternPreviewConduit()
    scriptcontext.sticky[CONDUIT_STICKY_KEY] = fresh
    return fresh
