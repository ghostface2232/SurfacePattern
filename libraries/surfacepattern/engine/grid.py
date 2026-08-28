#! python3
# Regular array engine (circle/slot/hex) producing UV-space placements.

import math
import random

from surfacepattern.core import mapping

DEFAULTS = {
    "shape": "circle",
    "size": 1.0,             # mm
    "spacing_x": 1.0,        # mm; clear gap between shape edges (not center distance)
    "spacing_y": 1.0,        # mm; clear gap between shape edges (not center distance)
    "grid_type": "square",   # "square" | "staggered" | "triangular"
    "rotation": 0.0,         # degrees, applied to every unit shape
    "jitter_position": 0.0,  # 0-100 (%)
    "jitter_size": 0.0,      # 0-100 (%)
    "jitter_rotation": 0.0,  # 0-100 (%)
    "seed": 0,
    "slot_ratio": 0.4,
}

MAX_ROWS = 1000
MAX_COLS = 1000
SURFACE_CANDIDATES_PER_POINT = 36
MAX_SURFACE_CANDIDATES = 120000
MAX_SURFACE_POINTS = 50000
MAX_DRAFT_SURFACE_CANDIDATES = 16000
DEFAULT_DRAFT_CAP = 1500


def param(session, key):
    """Session parameter with engine default fallback."""
    return session.params.get(key, DEFAULTS.get(key))


def generate(session):
    """Return [(face_record, u, v, size_mm, rotation_radians), ...] for the grid pattern."""
    if session.params.get("placement_mode", "uv") in ("surface", "world"):
        return _generate_surface_equal(session)
    return _generate_uv(session)


def _shape_extent(session):
    """Nominal shape footprint (x, y) in mm, used to convert gap spacing to center steps.

    Halftone mode modulates sizes up to halftone_size_max and stamp mode scales its
    unit-bounds stamps to stamp_size, so those are the extents guaranteeing the typed
    gap at the largest shapes. Rotation is ignored: the slot footprint assumes rotation 0.
    """
    mode = session.params.get("pattern_mode", "grid")
    if mode == "halftone":
        size = float(session.params.get("halftone_size_max", 1.0))
    elif mode == "stamp":
        size = float(session.params.get("stamp_size", 1.0))
    else:
        size = float(param(session, "size"))
    if mode != "stamp" and param(session, "shape") == "slot":
        return size, size * float(param(session, "slot_ratio"))
    return size, size


def _center_steps(session):
    """Center-to-center lattice steps (x, y) in mm: typed gap + shape footprint."""
    gap_x = max(float(param(session, "spacing_x")), 0.0)
    gap_y = max(float(param(session, "spacing_y")), 0.0)
    extent_x, extent_y = _shape_extent(session)
    return max(gap_x + extent_x, 1e-3), max(gap_y + extent_y, 1e-3)


def _jittered_attributes(session, rng):
    """One placement's (size, rotation_radians) with size/rotation jitter applied."""
    size = float(param(session, "size"))
    size_jitter = float(param(session, "jitter_size")) / 100.0
    if size_jitter > 0.0:
        size *= 1.0 + size_jitter * rng.uniform(-0.5, 0.5)
    rotation = math.radians(float(param(session, "rotation")))
    rotation_jitter = float(param(session, "jitter_rotation")) / 100.0
    if rotation_jitter > 0.0:
        rotation += rotation_jitter * rng.uniform(-math.pi, math.pi)
    return size, rotation


def _generate_uv(session):
    """UV mode: per-face lattice with mm spacing converted through local_scale row by row."""
    rng = random.Random(int(param(session, "seed")))
    placements = []
    for record in session.targets:
        placements.extend(_face_lattice(session, record, rng))
    return placements


def _generate_surface_equal(session):
    """Surface mode: isotropic Poisson-like placements independent of UV directions."""
    rng = random.Random(int(param(session, "seed")))
    extent_x, extent_y = _shape_extent(session)
    gap = max(float(param(session, "spacing_x")), 0.0)
    spacing = max(max(extent_x, extent_y) + gap, 1e-3)

    samplers = []
    estimated_total = 0
    for record in session.targets:
        sampler = mapping.SurfaceMetricSampler(record)
        area, max_area_scale = sampler.estimate_area()
        if area <= 1e-9 or max_area_scale <= 1e-12:
            continue
        estimated_count = max(1, int(math.ceil(area / (spacing * spacing))))
        estimated_total += estimated_count
        samplers.append((record, sampler, estimated_count, max_area_scale))
    if not samplers:
        return []

    point_limit = MAX_SURFACE_POINTS
    candidates_per_point = SURFACE_CANDIDATES_PER_POINT
    max_candidates = MAX_SURFACE_CANDIDATES
    if session.preview_quality == "draft":
        point_limit = max(int(session.params.get("draft_cap", DEFAULT_DRAFT_CAP)), 1)
        candidates_per_point = 8
        max_candidates = MAX_DRAFT_SURFACE_CANDIDATES
    target_count = min(estimated_total, point_limit)
    candidate_limit = min(
        max(target_count * candidates_per_point, 256), max_candidates
    )

    candidates = []
    for record, sampler, estimated_count, max_area_scale in samplers:
        share = estimated_count / float(estimated_total)
        budget = max(64, int(round(candidate_limit * share)))
        for _index in range(budget):
            u, v = rng.random(), rng.random()
            sample = sampler.sample(u, v)
            if sample is None:
                continue
            point, area_scale = sample
            if rng.random() * max_area_scale <= area_scale:
                candidates.append((record, u, v, point))
    rng.shuffle(candidates)

    spacing_squared = spacing * spacing
    cells = {}
    placements = []
    for record, u, v, point in candidates:
        cell = _spatial_cell(point, spacing)
        if _has_nearby_point(cells, cell, point, spacing_squared):
            continue
        cells.setdefault(cell, []).append(point)
        size, rotation = _jittered_attributes(session, rng)
        placements.append((record, u, v, size, rotation))
        if len(placements) >= target_count:
            break
    return placements


def _spatial_cell(point, spacing):
    """Integer 3D hash cell for one model-space point."""
    return (
        int(math.floor(point.X / spacing)),
        int(math.floor(point.Y / spacing)),
        int(math.floor(point.Z / spacing)),
    )


def _has_nearby_point(cells, cell, point, spacing_squared):
    """True when a point in this or a neighboring hash cell violates minimum spacing."""
    cx, cy, cz = cell
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for other in cells.get((cx + dx, cy + dy, cz + dz), ()):
                    px = point.X - other.X
                    py = point.Y - other.Y
                    pz = point.Z - other.Z
                    if px * px + py * py + pz * pz < spacing_squared:
                        return True
    return False


def _face_lattice(session, record, rng):
    step_x, step_y = _center_steps(session)
    grid_type = param(session, "grid_type")
    stagger = grid_type in ("staggered", "triangular")
    row_factor = math.sqrt(3.0) / 2.0 if grid_type == "triangular" else 1.0
    position_jitter = float(param(session, "jitter_position")) / 100.0

    out = []
    v = None
    row_index = 0
    while row_index < MAX_ROWS:
        # Re-evaluate scale at the current row so strongly curved faces stay uniform in 3D.
        probe_v = 0.0 if v is None else min(max(v, 0.0), 1.0)
        scale = mapping.local_scale(record, 0.5, probe_v)
        if scale is None or scale[0] <= 1e-9 or scale[1] <= 1e-9:
            break
        scale_u, scale_v = scale
        du = step_x / scale_u                # normalized-UV column step for this row
        dv = step_y * row_factor / scale_v   # normalized-UV step to the next row
        if v is None:
            v = dv * 0.5
        if v >= 1.0:
            break

        u = du * 0.5 + (du * 0.5 if stagger and row_index % 2 else 0.0)
        column = 0
        while u < 1.0 and column < MAX_COLS:
            uu, vv = u, v
            if position_jitter > 0.0:
                uu += position_jitter * du * rng.uniform(-0.5, 0.5)
                vv += position_jitter * dv * rng.uniform(-0.5, 0.5)
                uu = min(max(uu, 0.0), 1.0)
                vv = min(max(vv, 0.0), 1.0)
            if mapping.is_point_on_face(record, uu, vv):
                size, rotation = _jittered_attributes(session, rng)
                out.append((record, uu, vv, size, rotation))
            u += du
            column += 1
        v += dv
        row_index += 1
    return out
