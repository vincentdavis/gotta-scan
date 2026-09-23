"""Tests for foot measurement extraction, on synthetic meshes with known sizes."""

import tempfile

import numpy as np
import trimesh
from django.test import SimpleTestCase, TestCase, override_settings

from scans.analysis import foot
from scans.models import Scan

ANALYSIS_MEDIA_TMP = tempfile.mkdtemp(prefix='gottascan-analysis-media-')


def lofted_foot(length=260.0, max_width=100.0, height=65.0, ball_frac=0.72, rings=60, n=28):
    """A smooth foot-like solid: ellipse cross-sections lofted along +X, widest at
    ball_frac, resting with its sole near Y=0. Length/width/girth have known-ish
    values so the extractor can be checked against them."""
    ts = np.linspace(0.0, 1.0, rings)
    widths = max_width * (0.5 + 0.5 * np.exp(-((ts - ball_frac) ** 2) / (2 * 0.14 ** 2)))
    heights = height * (0.45 + 0.55 * np.exp(-((ts - 0.5) ** 2) / (2 * 0.28 ** 2)))
    theta = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)

    verts = []
    for t, w, h in zip(ts, widths, heights):
        x = t * length
        z = (w / 2.0) * np.cos(theta)
        y = (h / 2.0) + (h / 2.0) * np.sin(theta)  # sole ~0, top ~h
        verts.append(np.column_stack([np.full(n, x), y, z]))
    verts = np.vstack(verts)

    faces = []
    for r in range(rings - 1):
        for i in range(n):
            a = r * n + i
            b = r * n + (i + 1) % n
            c = (r + 1) * n + i
            d = (r + 1) * n + (i + 1) % n
            faces.append([a, b, d])
            faces.append([a, d, c])
    # End caps (fan to the ring centroid).
    for r, base in ((0, 0), (rings - 1, (rings - 1) * n)):
        centroid = verts[base:base + n].mean(axis=0)
        ci = len(verts)
        verts = np.vstack([verts, centroid])
        for i in range(n):
            a = base + i
            b = base + (i + 1) % n
            faces.append([a, b, ci] if r == 0 else [b, a, ci])

    mesh = trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)
    trimesh.repair.fix_normals(mesh)
    return mesh


class GirthTests(SimpleTestCase):
    def test_girth_of_cylinder_matches_circumference(self):
        # Cylinder radius 30 along X; a cut perpendicular to X is a circle.
        cyl = trimesh.creation.cylinder(radius=30.0, height=120.0)
        cyl.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [0, 1, 0]))
        girth = foot._section_girth(foot._section(cyl, x=0.0))
        self.assertAlmostEqual(girth, 2 * np.pi * 30.0, delta=2.0)  # ~188.5 mm


class OrientationTests(SimpleTestCase):
    def test_box_length_and_width(self):
        # Box L×H×W = 260×60×100, deliberately rotated so orientation must recover it.
        box = trimesh.creation.box(extents=[260.0, 60.0, 100.0])
        box.apply_transform(trimesh.transformations.rotation_matrix(0.6, [0, 1, 0]))
        m = foot.measure_foot(box)
        self.assertAlmostEqual(m.length_mm, 260.0, delta=3.0)
        # Uniform-width box: any slice is ~100mm.
        self.assertAlmostEqual(m.ball_width_mm, 100.0, delta=3.0)
        self.assertAlmostEqual(m.heel_width_mm, 100.0, delta=3.0)


class LoftedFootTests(SimpleTestCase):
    def setUp(self):
        self.mesh = lofted_foot(length=260.0, max_width=100.0, height=65.0, ball_frac=0.72)
        self.m = foot.measure_foot(self.mesh)

    def test_length(self):
        self.assertAlmostEqual(self.m.length_mm, 260.0, delta=5.0)

    def test_ball_location_and_width(self):
        # The widest section should be found near where we built it (72%).
        self.assertTrue(0.6 <= self.m.ball_location_fraction <= 0.82,
                        f"ball at {self.m.ball_location_fraction}")
        self.assertAlmostEqual(self.m.ball_width_mm, 100.0, delta=12.0)

    def test_heel_narrower_than_ball(self):
        self.assertLess(self.m.heel_width_mm, self.m.ball_width_mm)

    def test_girths_positive_and_ordered(self):
        # Ball girth should exceed instep girth on this shape (widest at the ball).
        self.assertGreater(self.m.ball_girth_mm, 0)
        self.assertGreater(self.m.instep_girth_mm, 0)
        self.assertGreater(self.m.ball_girth_mm, self.m.instep_girth_mm)

    def test_measurements_serialise(self):
        d = self.m.as_dict()
        self.assertEqual(set(d), {
            'length_mm', 'ball_width_mm', 'heel_width_mm', 'ball_girth_mm',
            'instep_girth_mm', 'arch_height_mm', 'ball_location_fraction',
        })


@override_settings(MEDIA_ROOT=ANALYSIS_MEDIA_TMP)
class FootAnalysisViewTests(TestCase):
    def _scan_with_mesh(self, scan_id='scan-foot-view'):
        scan = Scan.objects.create(scan_id=scan_id, status=Scan.Status.COMPLETE)
        scan.obj_dir.mkdir(parents=True, exist_ok=True)
        mesh = lofted_foot(length=260.0, max_width=100.0)  # mm
        mesh.apply_scale(0.001)  # worker OBJ is in metres; service scales back up
        mesh.export(scan.obj_dir / 'model.obj')
        return scan

    def test_analyze_and_display(self):
        scan = self._scan_with_mesh()
        response = self.client.post(f'/scans/{scan.scan_id}/analyze-foot/')
        self.assertEqual(response.status_code, 302)

        data = scan.foot_measurements()
        self.assertIsNotNone(data)
        self.assertAlmostEqual(data['length_mm'], 260.0, delta=6.0)

        html = self.client.get(f'/scans/{scan.scan_id}/').content.decode()
        self.assertIn('Foot measurements', html)
        self.assertIn('Foot length', html)

    def test_analyze_without_mesh_reports_error(self):
        scan = Scan.objects.create(scan_id='scan-foot-nomesh', status=Scan.Status.COMPLETE)
        response = self.client.post(f'/scans/{scan.scan_id}/analyze-foot/', follow=True)
        self.assertContains(response, 'No reconstructed mesh')
        self.assertIsNone(scan.foot_measurements())

    def test_get_does_not_run_analysis(self):
        scan = self._scan_with_mesh(scan_id='scan-foot-get')
        self.client.get(f'/scans/{scan.scan_id}/analyze-foot/')
        self.assertIsNone(scan.foot_measurements())
