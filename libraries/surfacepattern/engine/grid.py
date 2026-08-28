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
    """Surface mode: lattice steps follow true isocurve arc lengths in model units."""
    rng = random.Random(int(param(session, "seed")))
    placements = []
    for record in session.targets:
        placements.extend(_surface_equal_lattice(session, record, rng))
    return placements


def _surface_equal_lattice(session, record, rng):
    """Build one face lattice with exact row-wise U and center-line V arc spacing."""
    step_x, step_y = _center_steps(session)
    grid_type = param(session, "grid_type")
    stagger = grid_type in ("staggered", "triangular")
    row_factor = math.sqrt(3.0) / 2.0 if grid_type == "triangular" else 1.0
    row_step = step_y * row_factor
    position_jitter = float(param(session, "jitter_position")) / 100.0

    rows = mapping.equal_arc_parameters(
        record, "v", 0.5, row_step, max_count=MAX_ROWS
    )
    out = []
    for row_index, v in enumerate(rows):
        offset = 1.0 if stagger and row_index % 2 else 0.5
        columns = mapping.equal_arc_parameters(
            record, "u", v, step_x, offset=offset, max_count=MAX_COLS
        )
        for u in columns:
            uu, vv = u, v
            if position_jitter > 0.0:
                scale = mapping.local_scale(record, u, v)
                if scale is not None and scale[0] > 1e-9 and scale[1] > 1e-9:
                    uu += (
                        position_jitter * step_x * rng.uniform(-0.5, 0.5) / scale[0]
                    )
                    vv += (
                        position_jitter * row_step * rng.uniform(-0.5, 0.5) / scale[1]
                    )
                    uu = min(max(uu, 0.0), 1.0)
                    vv = min(max(vv, 0.0), 1.0)
            if mapping.is_point_on_face(record, uu, vv):
                size, rotation = _jittered_attributes(session, rng)
                out.append((record, uu, vv, size, rotation))
    return out


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
