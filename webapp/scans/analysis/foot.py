"""Extract footwear measurements from a scanned foot mesh.

The measurement set is the one last-makers and orthotics labs work from:
foot length, ball width, heel width, ball girth, instep girth, and arch height
(see the project research). All outputs are in millimetres — the LiDAR capture
gives true real-world scale, which is what makes this viable at all.

Canonical frame after orientation:
  +X = heel -> toe (length),  +Y = up (height),  +Z = medial-lateral (width).

Landmarks are heuristic (documented per measurement), not anatomically detected;
they are robust enough for a measurement report and are the place to refine once
validated against a real foot scan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass

import numpy as np

logger = logging.getLogger(__name__)

# Fraction of foot length (from the heel) where each landmark sits. The ball of
# the foot (metatarsophalangeal joints) is ~72% along; the instep ~50%. We refine
# the ball location by the actual width maximum, but fall back to these fractions.
BALL_FRACTION = 0.72
INSTEP_FRACTION = 0.50
HEEL_FRACTION = 0.08  # a slice just forward of the heel-most point for heel width


class FootMeasurementError(Exception):
    """The mesh could not be measured as a foot."""


@dataclass
class FootMeasurements:
    length_mm: float
    ball_width_mm: float
    heel_width_mm: float
    ball_girth_mm: float
    instep_girth_mm: float
    arch_height_mm: float
    # Where the ball landmark actually landed, as a fraction of length from the heel.
    ball_location_fraction: float

    def as_dict(self) -> dict:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def _principal_ground_axis(points_2d: np.ndarray) -> np.ndarray:
    """Unit vector of the longest spread in a set of 2D (ground-plane) points."""
    centered = points_2d - points_2d.mean(axis=0)
    # Covariance eigenvector with the largest eigenvalue = direction of max spread.
    cov = np.cov(centered, rowvar=False)
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    return eigenvectors[:, int(np.argmax(eigenvalues))]


def orient_foot(mesh, up_axis: str = 'y'):
    """Rotate a foot mesh into the canonical frame and drop it onto Y=0.

    Assumes the capture is gravity-aligned (Object Capture is), so the vertical
    axis is known; the heel->toe direction is the longest spread of the footprint
    projected onto the ground, and toe vs heel is disambiguated by where the foot
    is widest (the ball sits toward the toe).
    """
    import trimesh

    axis_index = {'x': 0, 'y': 1, 'z': 2}[up_axis]
    ground_indices = [i for i in range(3) if i != axis_index]

    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    ground = vertices[:, ground_indices]
    long_dir = _principal_ground_axis(ground)

    # Build a rotation that maps long_dir -> +X and up -> +Y in a right-handed frame.
    up = np.zeros(3)
    up[axis_index] = 1.0
    heel_toe = np.zeros(3)
    heel_toe[ground_indices[0]] = long_dir[0]
    heel_toe[ground_indices[1]] = long_dir[1]
    heel_toe /= np.linalg.norm(heel_toe)

    width_dir = np.cross(up, heel_toe)
    width_dir /= np.linalg.norm(width_dir)
    # Re-orthogonalise heel_toe against up so the basis is exactly orthonormal.
    heel_toe = np.cross(width_dir, up)

    rotation = np.eye(4)
    rotation[:3, 0] = heel_toe
    rotation[:3, 1] = up
    rotation[:3, 2] = width_dir
    # rotation currently maps canonical axes -> world; we want world -> canonical.
    rotation[:3, :3] = rotation[:3, :3].T

    oriented = mesh.copy()
    oriented.apply_transform(rotation)

    # Disambiguate heel/toe: the widest cross-section (the ball) sits toward the toe.
    # If it currently lands in the rear half, flip 180° about the up axis.
    verts = np.asarray(oriented.vertices)
    x = verts[:, 0]
    x_min, x_max = x.min(), x.max()
    span = x_max - x_min
    if span > 0:
        widest_x = _widest_section_x(oriented)
        if (widest_x - x_min) / span < 0.5:
            flip = trimesh.transformations.rotation_matrix(np.pi, [0, 1, 0])
            oriented.apply_transform(flip)

    # Drop the sole to Y = 0 and the heel to X = 0 so measurements read from the heel.
    verts = np.asarray(oriented.vertices)
    oriented.apply_translation([-verts[:, 0].min(), -verts[:, 1].min(), 0.0])
    return oriented


def _section(mesh, x: float):
    """Cross-section of the foot at length position x (perpendicular to +X)."""
    return mesh.section(plane_origin=[x, 0, 0], plane_normal=[1, 0, 0])


def _section_width(section) -> float:
    """Medial-lateral (Z) extent of a cross-section — the foot width there.

    Taken from the section's 3D vertices so it's independent of mesh vertex
    density and of how to_planar happens to orient its 2D axes.
    """
    if section is None:
        return 0.0
    verts = np.asarray(section.vertices)
    if len(verts) < 2:
        return 0.0
    return float(verts[:, 2].max() - verts[:, 2].min())


def _section_girth(section) -> float:
    """Perimeter of a cross-section outline — a girth (wraps over top and sole)."""
    if section is None:
        return 0.0
    planar, _ = section.to_planar()
    try:
        polygons = planar.polygons_full
    except Exception:
        polygons = None
    if not polygons:
        return float(planar.length)
    largest = max(polygons, key=lambda p: p.area)
    return float(largest.exterior.length)


def _widest_section_x(mesh, samples: int = 40) -> float:
    """X position (length axis) where the foot cross-section is widest in Z."""
    verts = np.asarray(mesh.vertices)
    x = verts[:, 0]
    x_min, x_max = x.min(), x.max()
    # Sample strictly inside the span; sections exactly at the ends can be empty.
    xs = np.linspace(x_min, x_max, samples + 2)[1:-1]
    best_x, best_width = (x_min + x_max) / 2, -1.0
    for xi in xs:
        width = _section_width(_section(mesh, xi))
        if width > best_width:
            best_width, best_x = width, xi
    return best_x


def measure_foot(mesh, up_axis: str = 'y') -> FootMeasurements:
    """Measure a foot mesh (already in millimetres) and return the measurement set."""
    import trimesh

    if not isinstance(mesh, trimesh.Trimesh):
        raise FootMeasurementError('Expected a triangular mesh.')
    if mesh.is_empty or len(mesh.faces) == 0:
        raise FootMeasurementError('Mesh is empty.')

    foot = orient_foot(mesh, up_axis=up_axis)
    verts = np.asarray(foot.vertices)
    length = float(verts[:, 0].max() - verts[:, 0].min())
    if length <= 0:
        raise FootMeasurementError('Degenerate foot length.')

    x_min = float(verts[:, 0].min())  # ~0 after orient drops heel to X=0

    ball_x = _widest_section_x(foot)
    ball_fraction = (ball_x - x_min) / length
    # Guard against a spurious widest slice at the very toe/heel.
    if not 0.55 <= ball_fraction <= 0.85:
        ball_x = x_min + length * BALL_FRACTION
        ball_fraction = BALL_FRACTION

    instep_x = x_min + length * INSTEP_FRACTION
    heel_x = x_min + length * HEEL_FRACTION

    ball_section = _section(foot, ball_x)
    ball_width = _section_width(ball_section)
    heel_width = _section_width(_section(foot, heel_x))
    ball_girth = _section_girth(ball_section)
    instep_girth = _section_girth(_section(foot, instep_x))

    half_band = max(length * 0.03, 3.0)

    # Arch height: the highest point of the sole under the mid-foot. With the sole
    # dropped to Y=0, a flat sole reads ~0; a real arch lifts off the ground, so we
    # take the minimum Y of the underside in the mid-foot band as the clearance.
    arch_height = _arch_height(foot, instep_x, half_band * 3)

    return FootMeasurements(
        length_mm=length,
        ball_width_mm=ball_width,
        heel_width_mm=heel_width,
        ball_girth_mm=ball_girth,
        instep_girth_mm=instep_girth,
        arch_height_mm=arch_height,
        ball_location_fraction=round(ball_fraction, 3),
    )


def measure_foot_for_scan(scan) -> dict:
    """Measure a scan's reconstructed mesh as a foot and cache the result.

    Loads the master OBJ (metres), scales to millimetres, measures, and writes
    foot_measurements.json next to the model. Returns the measurement dict.
    """
    import trimesh

    obj_files = sorted(scan.obj_dir.glob('*.obj')) if scan.obj_dir.is_dir() else []
    if not obj_files:
        raise FootMeasurementError('No reconstructed mesh to measure yet.')

    mesh = trimesh.load(obj_files[0], force='mesh')
    if mesh.is_empty or len(mesh.faces) == 0:
        raise FootMeasurementError('Reconstructed mesh is empty.')
    mesh.apply_scale(1000.0)  # metres -> millimetres

    data = measure_foot(mesh).as_dict()
    scan.models_dir.mkdir(parents=True, exist_ok=True)
    (scan.models_dir / 'foot_measurements.json').write_text(json.dumps(data, indent=2))
    return data


def _arch_height(mesh, x: float, half_band: float) -> float:
    """Ground clearance of the sole under the mid-foot band.

    For each thin lengthwise column in the band, the lowest vertex is the sole;
    the arch height is how far the *highest* such sole point rises above Y=0
    (the medial arch lifts off the ground between heel and ball).
    """
    verts = np.asarray(mesh.vertices)
    band = verts[np.abs(verts[:, 0] - x) <= half_band]
    if len(band) < 5:
        return 0.0
    # Bin across the width; the sole in each bin is the lowest point.
    z = band[:, 2]
    z_min, z_max = z.min(), z.max()
    if z_max - z_min <= 0:
        return 0.0
    bins = np.linspace(z_min, z_max, 12)
    sole_heights = []
    for i in range(len(bins) - 1):
        col = band[(z >= bins[i]) & (z < bins[i + 1])]
        if len(col):
            sole_heights.append(col[:, 1].min())
    if not sole_heights:
        return 0.0
    return float(max(sole_heights))
