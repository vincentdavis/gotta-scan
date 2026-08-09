"""Per-destination exports from a scan's master OBJ mesh.

PhotogrammetrySession's OBJ output is in meters. Destinations differ:
- GLB: glTF spec defines meters -> exported as-is (web viewer, Blender).
- 3MF: spec unit attribute set to millimeter, geometry scaled x1000 (print, primary).
- STL: unitless; slicers assume millimeters -> geometry scaled x1000 (print, legacy).
- OBJ (OnShape variant): scaled to millimeters and decimated to ~100k faces;
  the user picks 'millimeter' at OnShape import.

A manifest (exports.json) records real-world dimensions and watertightness so the
UI can show a scale sanity-check before anyone prints.
"""

import json
import logging
import shutil
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

ONSHAPE_TARGET_FACES = 100_000

THREEMF_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

THREEMF_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


class ExportError(Exception):
    """The master mesh is missing or unusable."""


def _load_master_mesh(obj_dir: Path):
    """Load the worker's OBJ output as a single triangulated mesh (meters)."""
    import trimesh

    obj_files = sorted(obj_dir.glob('*.obj'))
    if not obj_files:
        raise ExportError('No OBJ master mesh found.')
    loaded = trimesh.load(obj_files[0], force='mesh')
    if loaded.is_empty or len(loaded.faces) == 0:
        raise ExportError('Master mesh is empty.')
    return loaded


def _basic_repair(mesh) -> None:
    """Light cleanup only: dedupe and fix normals.

    Deliberately does NOT fill holes — the open boundary is left pristine so the
    GLB/viewer show the scan as captured, and so _make_printable's close_holes
    sees an untouched boundary loop (a partial trimesh fan-fill here would leave
    a boundary PyMeshLab can no longer close).
    """
    import trimesh

    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    trimesh.repair.fix_normals(mesh)


def _write_3mf(mesh_mm, path: Path) -> None:
    """Minimal spec-compliant 3MF: single object, unit explicitly millimeter."""
    vertices = '\n'.join(
        f'          <vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>'
        for x, y, z in mesh_mm.vertices
    )
    triangles = '\n'.join(
        f'          <triangle v1="{a}" v2="{b}" v3="{c}"/>'
        for a, b, c in mesh_mm.faces
    )
    model_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
  xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>
{vertices}
        </vertices>
        <triangles>
{triangles}
        </triangles>
      </mesh>
    </object>
  </resources>
  <build>
    <item objectid="1"/>
  </build>
</model>
"""
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('[Content_Types].xml', THREEMF_CONTENT_TYPES)
        zf.writestr('_rels/.rels', THREEMF_RELS)
        zf.writestr('3D/3dmodel.model', model_xml)


def _decimated(mesh, target_faces: int):
    if len(mesh.faces) <= target_faces:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=target_faces)
    except Exception:
        logger.exception('Decimation failed; exporting full-resolution mesh')
        return mesh


def _make_printable(mesh):
    """Close boundary holes to produce a watertight, printable solid.

    Photogrammetry scans are open where the camera never saw a surface (e.g. an
    object's underside). PyMeshLab's close_holes robustly bridges the non-convex
    boundary loops that trimesh's fan-fill can't handle; we fall back to trimesh
    if PyMeshLab is unavailable. Returns (mesh, is_watertight).
    """
    import trimesh

    def trimesh_fallback():
        filled = mesh.copy()
        trimesh.repair.fill_holes(filled)
        trimesh.repair.fix_normals(filled)
        return filled, bool(filled.is_watertight)

    try:
        import numpy as np
        import pymeshlab
    except ImportError:
        logger.warning('PyMeshLab unavailable; falling back to trimesh hole fill')
        return trimesh_fallback()

    try:
        ms = pymeshlab.MeshSet()
        ms.add_mesh(pymeshlab.Mesh(
            vertex_matrix=np.asarray(mesh.vertices, dtype=np.float64),
            face_matrix=np.asarray(mesh.faces, dtype=np.int32),
        ))
        ms.meshing_remove_duplicate_vertices()
        ms.meshing_remove_duplicate_faces()
        ms.meshing_remove_unreferenced_vertices()
        ms.meshing_repair_non_manifold_edges()
        # maxholesize is in edges: large enough to close a full underside opening.
        ms.meshing_close_holes(maxholesize=1_000_000, selfintersection=False)
        out = ms.current_mesh()
        filled = trimesh.Trimesh(
            vertices=out.vertex_matrix(), faces=out.face_matrix(), process=True,
        )
        trimesh.repair.fix_normals(filled)
        if filled.is_watertight:
            return filled, True
        logger.warning('close_holes left mesh open; trying trimesh fill on top')
        trimesh.repair.fill_holes(filled)
        return filled, bool(filled.is_watertight)
    except Exception:
        logger.exception('PyMeshLab hole fill failed; falling back to trimesh')
        return trimesh_fallback()


def generate_exports(scan) -> dict:
    """Build all export files under models/exports/ and write exports.json."""
    obj_dir = scan.models_dir / 'obj'
    exports_dir = scan.models_dir / 'exports'
    # Never mix runs: a mid-run failure must not leave new geometry files
    # alongside a previous run's manifest describing a different mesh.
    if exports_dir.exists():
        shutil.rmtree(exports_dir)
    exports_dir.mkdir(parents=True, exist_ok=True)

    mesh = _load_master_mesh(obj_dir)
    _basic_repair(mesh)

    files: dict[str, str] = {}

    # GLB — meters, keeps textures/materials where present.
    glb_path = exports_dir / 'model.glb'
    mesh.export(glb_path)
    files['glb'] = glb_path.name

    mesh_mm = mesh.copy()
    mesh_mm.apply_scale(1000.0)

    # Print exports get a hole-filled watertight solid; the OnShape reference and
    # GLB keep the raw scan (open underside, faithful surface and texture).
    print_mesh, print_watertight = _make_printable(mesh_mm)

    stl_path = exports_dir / 'model-mm.stl'
    print_mesh.export(stl_path)
    files['stl'] = stl_path.name

    threemf_path = exports_dir / 'model.3mf'
    _write_3mf(print_mesh, threemf_path)
    files['3mf'] = threemf_path.name

    onshape_mesh = _decimated(mesh_mm, ONSHAPE_TARGET_FACES)
    onshape_path = exports_dir / 'model-onshape-mm.obj'
    onshape_mesh.export(onshape_path)
    files['onshape_obj'] = onshape_path.name

    volume_cm3 = round(float(abs(print_mesh.volume)) / 1000.0, 1) if print_watertight else None
    extents_mm = [round(float(v), 1) for v in mesh_mm.extents]
    manifest = {
        'files': files,
        'dimensions_mm': extents_mm,
        'watertight': bool(mesh.is_watertight),  # the raw mesh shown in the viewer
        'print_watertight': bool(print_watertight),  # the STL/3MF sent to a slicer
        'faces': int(len(mesh.faces)),
        'print_faces': int(len(print_mesh.faces)),
        'onshape_faces': int(len(onshape_mesh.faces)),
        'volume_cm3': volume_cm3,
        'units_note': 'GLB in meters; STL/3MF/OnShape-OBJ in millimeters (pick mm at OnShape import).',
    }
    (exports_dir / 'exports.json').write_text(json.dumps(manifest, indent=2))
    return manifest
