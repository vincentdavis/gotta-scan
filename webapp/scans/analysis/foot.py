"""Django-side foot analysis: thin wrapper over the shared geometry package.

The measurement geometry lives in gottascan_geometry (shared with the desktop
sidecar); this module adds the scan/model-file plumbing.
"""

from __future__ import annotations

import json

# Re-export so existing imports (incl. the private helpers used by tests) keep working.
from gottascan_geometry.foot import (  # noqa: F401
    BALL_FRACTION,
    HEEL_FRACTION,
    INSTEP_FRACTION,
    FootMeasurementError,
    FootMeasurements,
    load_foot_mesh,
    measure_foot,
    orient_foot,
    _arch_height,
    _section,
    _section_girth,
    _section_width,
    _widest_section_x,
)


def measure_foot_for_scan(scan) -> dict:
    """Measure a scan's reconstructed mesh as a foot and cache the result.

    Loads the master OBJ (metres), scales to millimetres, measures, and writes
    foot_measurements.json next to the model. Returns the measurement dict.
    """
    obj_files = sorted(scan.obj_dir.glob('*.obj')) if scan.obj_dir.is_dir() else []
    if not obj_files:
        raise FootMeasurementError('No reconstructed mesh to measure yet.')

    mesh = load_foot_mesh(obj_files[0])
    data = measure_foot(mesh).as_dict()
    scan.models_dir.mkdir(parents=True, exist_ok=True)
    (scan.models_dir / 'foot_measurements.json').write_text(json.dumps(data, indent=2))
    return data
