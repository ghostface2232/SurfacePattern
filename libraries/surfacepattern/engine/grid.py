#! python3
# Regular array engine (circle/slot/hex) producing UV-space placements.

import math
import random

from surfacepattern.core import mapping

DEFAULTS = {
    "shape": "circle",
    "size": 4.0,             # mm
    "spacing_x": 10.0,       # mm
    "spacing_y": 10.0,       # mm
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
    if session.params.get("placement_mode", "uv") == "world":
        return _generate_world(session)
    return _generate_uv(session)


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


def _face_lattice(session, record, rng):
    spacing_x = max(float(param(session, "spacing_x")), 1e-3)
    spacing_y = max(float(param(session, "spacing_y")), 1e-3)
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
        du = spacing_x / scale_u                # normalized-UV column step for this row
        dv = spacing_y * row_factor / scale_v   # normalized-UV step to the next row
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


def _generate_world(session):
    """World mode: planar lattice in plane coordinates, projected to faces by mapping."""
    rng = random.Random(int(param(session, "seed")))
    plane = session.params.get("projection_plane")
    if plane is None:
        plane = mapping.default_projection_plane(session.targets)
    if plane is None:
        return []
    extent = mapping.plane_grid_extent(session.targets, plane)
    if extent is None:
        return []
    s_min, s_max, t_min, t_max = extent

    spacing_x = max(float(param(session, "spacing_x")), 1e-3)
    spacing_y = max(float(param(session, "spacing_y")), 1e-3)
    grid_type = param(session, "grid_type")
    stagger = grid_type in ("staggered", "triangular")
    row_factor = math.sqrt(3.0) / 2.0 if grid_type == "triangular" else 1.0
    position_jitter = float(param(session, "jitter_position")) / 100.0
    row_step = spacing_y * row_factor

    points = []
    row_index = 0
    t = t_min + row_step * 0.5
    while t < t_max and row_index < MAX_ROWS:
        s = s_min + spacing_x * 0.5 + (spacing_x * 0.5 if stagger and row_index % 2 else 0.0)
        column = 0
        while s < s_max and column < MAX_COLS:
            ss, tt = s, t
            if position_jitter > 0.0:
                ss += position_jitter * spacing_x * rng.uniform(-0.5, 0.5)
                tt += position_jitter * row_step * rng.uniform(-0.5, 0.5)
            points.append((ss, tt))
            s += spacing_x
            column += 1
        t += row_step
        row_index += 1

    placements = []
    for hit in mapping.project_plane_points(session.targets, plane, points):
        if hit is None:
            continue
        record, u, v = hit
        size, rotation = _jittered_attributes(session, rng)
        placements.append((record, u, v, size, rotation))
    return placements
