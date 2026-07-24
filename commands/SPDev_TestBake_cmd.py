#! python3
# Dev-only command: visual checks for the bake + trim pipeline against test-geometry.3dm (not published).
#
# Runs a coarse circle grid on picked targets and executes bake_with_trim: expect
# baked curves under SurfacePattern::grid_NN, perforated faces, and a hidden
# original copy under SurfacePattern::originals. One undo restores the file.

import Rhino
import scriptcontext

from surfacepattern.core import bake, mapping, session

TARGET_DIVISIONS = 6   # coarse lattice: few holes, fast trim


def main():
    """Pick targets, bake + trim a coarse circle grid, and report traceability."""
    current = session.get_session()
    if not session.pick_targets(current):
        Rhino.RhinoApp.WriteLine("SPDev_TestBake: nothing selected.")
        return

    scale = mapping.local_scale(current.targets[0], 0.5, 0.5)
    step = max(scale) / TARGET_DIVISIONS if scale else 10.0
    current.params.update(
        {
            "pattern_mode": "grid",
            "shape": "circle",
            "size": step * 0.5,
            "spacing_x": step * 0.5,
            "spacing_y": step * 0.5,
            "grid_type": "square",
            "jitter_position": 0.0,
            "jitter_size": 0.0,
            "jitter_rotation": 0.0,
            "seed": 0,
            "bake_keep_original": True,
        }
    )
    current.request_recompute(False)

    holes = bake.bake_with_trim(current)
    tagged = sum(
        1
        for rhobj in scriptcontext.doc.Objects
        if rhobj.Attributes.GetUserString(bake.PARAMS_USERSTRING_KEY)
    )
    Rhino.RhinoApp.WriteLine(
        "SPDev_TestBake: {} hole(s) trimmed, {} baked curve(s) carry the params "
        "UserString; check SurfacePattern layers, then undo once to restore.".format(
            holes, tagged
        )
    )


main()
