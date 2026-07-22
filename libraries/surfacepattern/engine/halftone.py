#! python3
# r: numpy
# Halftone engine: attractor-driven size/density falloff in parameter space.

import math

from surfacepattern.core import mapping
from surfacepattern.engine import grid

DEFAULTS = {
    "halftone_radius": 50.0,     # mm; attractor influence radius
    "halftone_profile": "linear",  # "linear" | "smooth" | "gaussian"
    "halftone_invert": False,    # False: large near attractors
    "halftone_size_min": 1.0,    # mm
    "halftone_size_max": 6.0,    # mm
    "halftone_cull": 0.0,        # mm; shapes below this size are dropped, 0 disables
}
DEFAULT_DRAFT_CAP = 1500


def param(session, key):
    """Session parameter with engine default fallback."""
    return session.params.get(key, DEFAULTS.get(key))


def _falloff(distance, radius, profile, invert):
    """Normalized falloff 0-1 from an attractor distance; 1 near the attractor by default."""
    x = min(max(distance / radius, 0.0), 1.0) if radius > 0.0 else 1.0
    if profile == "smooth":
        value = 1.0 - (3.0 * x * x - 2.0 * x * x * x)
    elif profile == "gaussian":
        value = math.exp(-4.5 * x * x)  # sigma = radius / 3
    else:
        value = 1.0 - x
    return 1.0 - value if invert else value


def _min_distances(points, attractor_points, attractor_curves):
    """Min distance from each 3D point to any attractor; numpy-vectorized when point-only."""
    if attractor_points and not attractor_curves:
        try:
            import numpy

            targets = numpy.array([[p.X, p.Y, p.Z] for p in attractor_points])
            samples = numpy.array([[p.X, p.Y, p.Z] for p in points])
            deltas = samples[:, None, :] - targets[None, :, :]
            return numpy.sqrt((deltas * deltas).sum(axis=2)).min(axis=1).tolist()
        except ImportError:
            pass

    distances = []
    for point in points:
        best = None
        for attractor in attractor_points:
            distance = point.DistanceTo(attractor)
            if best is None or distance < best:
                best = distance
        for curve in attractor_curves:
            ok, t = curve.ClosestPoint(point)
            if not ok:
                continue
            distance = point.DistanceTo(curve.PointAt(t))
            if best is None or distance < best:
                best = distance
        distances.append(best if best is not None else float("inf"))
    return distances


def generate(session):
    """Grid placements with sizes modulated by distance to the nearest attractor."""
    placements = grid.generate(session)
    if not placements:
        return []

    # Draft: thin to the display cap BEFORE the O(points x attractors) distance pass.
    if session.preview_quality == "draft":
        cap = session.params.get("draft_cap", DEFAULT_DRAFT_CAP)
        total = len(placements)
        if total > cap:
            placements = [placements[(i * total) // cap] for i in range(cap)]

    attractor_points, attractor_curves = session.resolve_attractors()
    if not attractor_points and not attractor_curves:
        # No attractors: fall back to each face's UV center so the mode shows results instantly.
        attractor_points = [mapping.point_at(record, 0.5, 0.5) for record in session.targets]

    points = [mapping.point_at(record, u, v) for record, u, v, _size, _rotation in placements]
    distances = _min_distances(points, attractor_points, attractor_curves)

    radius = float(param(session, "halftone_radius"))
    profile = param(session, "halftone_profile")
    invert = bool(param(session, "halftone_invert"))
    size_min = float(param(session, "halftone_size_min"))
    size_max = float(param(session, "halftone_size_max"))
    cull = float(param(session, "halftone_cull"))
    base_size = max(float(grid.param(session, "size")), 1e-9)

    out = []
    for placement, distance in zip(placements, distances):
        record, u, v, size, rotation = placement
        value = _falloff(distance, radius, profile, invert)
        # size / base_size preserves the grid engine's per-placement size jitter.
        final = (size_min + value * (size_max - size_min)) * (size / base_size)
        if final <= 0.0 or (cull > 0.0 and final < cull):
            continue
        out.append((record, u, v, final, rotation))
    return out
