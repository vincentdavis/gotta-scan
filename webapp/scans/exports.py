"""Django-side exports: thin wrapper over the shared geometry package.

The export geometry lives in gottascan_geometry.exports (shared with the desktop
sidecar); this module adds the scan model-path plumbing.
"""

# Re-export so existing imports keep working (api.py imports ExportError).
from gottascan_geometry.exports import (  # noqa: F401
    ONSHAPE_TARGET_FACES,
    ExportError,
)
from gottascan_geometry.exports import generate_exports as _generate_exports


def generate_exports(scan) -> dict:
    """Build all export files under the scan's models/exports/ and write exports.json."""
    return _generate_exports(scan.obj_dir, scan.exports_dir)
