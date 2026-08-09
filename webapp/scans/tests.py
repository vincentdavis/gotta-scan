import io
import json
import tempfile
import zipfile
from pathlib import Path

from django.test import TestCase, override_settings

from .models import Scan

MEDIA_TMP = tempfile.mkdtemp(prefix='gottascan-test-media-')
WORKER_HEADERS = {'X-Worker-Token': 'test-worker-token'}

# A 10cm cube in meters — same shape the worker's --simulate mode produces.
CUBE_OBJ = '\n'.join(
    ['# test cube']
    + [f'v {x} {y} {z}' for x, y, z in [
        (-0.05, -0.05, -0.05), (0.05, -0.05, -0.05), (0.05, 0.05, -0.05), (-0.05, 0.05, -0.05),
        (-0.05, -0.05, 0.05), (0.05, -0.05, 0.05), (0.05, 0.05, 0.05), (-0.05, 0.05, 0.05),
    ]]
    + [f'f {a} {b} {c}' for a, b, c in [
        (1, 3, 2), (1, 4, 3), (5, 6, 7), (5, 7, 8),
        (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6),
        (3, 4, 8), (3, 8, 7), (4, 1, 5), (4, 5, 8),
    ]]
) + '\n'


def make_obj_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        zf.writestr('model.obj', CUBE_OBJ)
    return buffer.getvalue()


def make_bundle(scan_id='scan-20260722-120000-abcd', image_count=3, metadata=True):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as zf:
        if metadata:
            zf.writestr(
                f'{scan_id}/metadata.json',
                json.dumps({'name': scan_id, 'captureMode': 'object', 'imageCount': image_count}),
            )
        for i in range(image_count):
            zf.writestr(f'{scan_id}/Images/img_{i:04d}.heic', b'not-a-real-heic')
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=MEDIA_TMP, WORKER_TOKEN='test-worker-token', UPLOAD_TOKEN='')
class UploadTests(TestCase):
    def upload(self, body, scan_id='scan-20260722-120000-abcd', **extra_headers):
        return self.client.put(
            '/api/scans/upload',
            data=body,
            content_type='application/zip',
            headers={'X-Scan-Id': scan_id, **extra_headers},
        )

    def test_upload_creates_queued_scan(self):
        response = self.upload(make_bundle())
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id='scan-20260722-120000-abcd')
        self.assertEqual(scan.status, Scan.Status.QUEUED)
        self.assertEqual(scan.image_count, 3)
        self.assertEqual(scan.capture_mode, 'object')
        self.assertTrue(scan.bundle_path.exists())
        self.assertTrue((scan.extracted_dir / scan.scan_id / 'Images').is_dir())

    def test_upload_requires_scan_id(self):
        response = self.client.put('/api/scans/upload', data=make_bundle(), content_type='application/zip')
        self.assertEqual(response.status_code, 400)

    def test_rejects_traversal_scan_id(self):
        response = self.upload(make_bundle(), scan_id='../evil')
        self.assertEqual(response.status_code, 400)

    def test_rejects_zip_slip_bundle(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('../../outside.txt', 'bad')
            zf.writestr('scan-x/Images/img_0001.heic', b'x')
        response = self.upload(buffer.getvalue(), scan_id='scan-zipslip')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(Scan.objects.get(scan_id='scan-zipslip').status, Scan.Status.FAILED)

    def test_rejects_bundle_without_images(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('scan-empty/metadata.json', '{}')
        response = self.upload(buffer.getvalue(), scan_id='scan-empty')
        self.assertEqual(response.status_code, 422)

    def test_rejects_empty_body(self):
        response = self.upload(b'')
        self.assertEqual(response.status_code, 400)

    def test_reupload_replaces_and_requeues(self):
        self.upload(make_bundle(image_count=2))
        response = self.upload(make_bundle(image_count=5))
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id='scan-20260722-120000-abcd')
        self.assertEqual(scan.image_count, 5)
        self.assertEqual(scan.status, Scan.Status.QUEUED)

    @override_settings(UPLOAD_TOKEN='sekrit')
    def test_upload_token_enforced_when_set(self):
        self.assertEqual(self.upload(make_bundle()).status_code, 403)
        response = self.upload(make_bundle(), **{'X-Upload-Token': 'sekrit'})
        self.assertEqual(response.status_code, 200)

    @override_settings(SCAN_MAX_EXTRACTED_BYTES=1024 * 1024)
    def test_zip_bomb_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('scan-bomb/Images/img_0001.heic', b'\x00' * (2 * 1024 * 1024))
        response = self.upload(buffer.getvalue(), scan_id='scan-bomb')
        self.assertEqual(response.status_code, 422)
        scan = Scan.objects.get(scan_id='scan-bomb')
        self.assertEqual(scan.status, Scan.Status.FAILED)
        # Partial extraction must have been cleaned up.
        self.assertFalse(scan.extracted_dir.exists())

    def test_interrupted_upload_preserves_existing_bundle(self):
        from django.test import RequestFactory

        from .api import upload_bundle

        self.upload(make_bundle(image_count=2))
        scan = Scan.objects.get(scan_id='scan-20260722-120000-abcd')
        good_bundle = scan.bundle_path.read_bytes()

        class DisconnectingStream:
            def read(self, size=-1):
                raise OSError('client disconnected')

        def interrupted_put(scan_id):
            request = RequestFactory().put(
                '/api/scans/upload', data=b'', content_type='application/zip',
                headers={'X-Scan-Id': scan_id},
            )
            request._stream = DisconnectingStream()
            return upload_bundle(request)

        # Existing scan: the previous good bundle and status must survive.
        response = interrupted_put(scan.scan_id)
        self.assertEqual(response.status_code, 400)
        scan.refresh_from_db()
        self.assertEqual(scan.bundle_path.read_bytes(), good_bundle)
        self.assertEqual(scan.status, Scan.Status.QUEUED)
        self.assertFalse((scan.scan_dir / 'bundle.zip.part').exists())

        # Fresh scan: the row must not linger in 'uploaded' limbo.
        response = interrupted_put('scan-fresh-interrupted')
        self.assertEqual(response.status_code, 400)
        fresh = Scan.objects.get(scan_id='scan-fresh-interrupted')
        self.assertEqual(fresh.status, Scan.Status.FAILED)


@override_settings(MEDIA_ROOT=MEDIA_TMP, WORKER_TOKEN='test-worker-token', UPLOAD_TOKEN='')
class WorkerFlowTests(TestCase):
    def setUp(self):
        self.client.put(
            '/api/scans/upload',
            data=make_bundle(),
            content_type='application/zip',
            headers={'X-Scan-Id': 'scan-20260722-120000-abcd'},
        )

    def test_claim_requires_token(self):
        self.assertEqual(self.client.post('/api/worker/claim').status_code, 403)

    def test_claim_returns_job_then_none(self):
        response = self.client.post('/api/worker/claim', headers=WORKER_HEADERS)
        self.assertEqual(response.status_code, 200)
        job = response.json()['job']
        self.assertEqual(job['scan_id'], 'scan-20260722-120000-abcd')
        self.assertEqual(Scan.objects.get(scan_id=job['scan_id']).status, Scan.Status.PROCESSING)
        # Queue is now empty.
        self.assertIsNone(self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job'])

    def test_bundle_download(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.get(job['bundle_url'], headers=WORKER_HEADERS)
        self.assertEqual(response.status_code, 200)
        payload = b''.join(response.streaming_content)
        self.assertTrue(zipfile.ZipFile(io.BytesIO(payload)).namelist())

    def test_complete_stores_model(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'fake-usdz-bytes'), 'log': 'processing complete'},
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id=job['scan_id'])
        self.assertEqual(scan.status, Scan.Status.COMPLETE)
        self.assertTrue(scan.has_model)
        self.assertEqual(scan.model_usdz_path.read_bytes(), b'fake-usdz-bytes')

    def test_fail_records_error(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/fail",
            data={'error': 'not enough overlap', 'log': 'tail'},
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id=job['scan_id'])
        self.assertEqual(scan.status, Scan.Status.FAILED)
        self.assertEqual(scan.error_message, 'not enough overlap')

    def test_requeue(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.client.post(f"/api/worker/{job['scan_id']}/fail", data={'error': 'x'}, headers=WORKER_HEADERS)
        response = self.client.post(f"/api/worker/{job['scan_id']}/requeue", headers=WORKER_HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Scan.objects.get(scan_id=job['scan_id']).status, Scan.Status.QUEUED)

    def test_stale_processing_scan_is_requeued_on_next_claim(self):
        from datetime import timedelta
        from django.utils import timezone

        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        # Simulate a worker that died 3 hours ago.
        Scan.objects.filter(scan_id=job['scan_id']).update(
            claimed_at=timezone.now() - timedelta(hours=3)
        )
        reclaimed = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.assertIsNotNone(reclaimed)
        self.assertEqual(reclaimed['scan_id'], job['scan_id'])

    def test_fail_does_not_downgrade_complete(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'model')},
            headers=WORKER_HEADERS,
        )
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/fail", data={'error': 'late'}, headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('ignored'))
        self.assertEqual(Scan.objects.get(scan_id=job['scan_id']).status, Scan.Status.COMPLETE)

    def test_reupload_clears_previous_model_and_completion(self):
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'old-model'), 'log': 'old log'},
            headers=WORKER_HEADERS,
        )
        response = self.client.put(
            '/api/scans/upload',
            data=make_bundle(image_count=4),
            content_type='application/zip',
            headers={'X-Scan-Id': job['scan_id']},
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id=job['scan_id'])
        self.assertEqual(scan.status, Scan.Status.QUEUED)
        self.assertFalse(scan.has_model)
        self.assertIsNone(scan.completed_at)
        self.assertEqual(scan.worker_log, '')


@override_settings(MEDIA_ROOT=MEDIA_TMP, WORKER_TOKEN='test-worker-token', UPLOAD_TOKEN='')
class ExportTests(TestCase):
    def complete_with_obj(self):
        self.client.put(
            '/api/scans/upload',
            data=make_bundle(),
            content_type='application/zip',
            headers={'X-Scan-Id': 'scan-20260722-120000-abcd'},
        )
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={
                'usdz': io.BytesIO(b'fake-usdz'),
                'obj_zip': io.BytesIO(make_obj_zip()),
                'log': 'done',
            },
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        return Scan.objects.get(scan_id=job['scan_id'])

    def test_complete_with_obj_generates_exports(self):
        scan = self.complete_with_obj()
        self.assertEqual(scan.status, Scan.Status.COMPLETE)
        manifest = scan.exports_manifest()
        self.assertIsNotNone(manifest)
        # 10cm cube in meters -> 100mm in every axis.
        self.assertEqual(manifest['dimensions_mm'], [100.0, 100.0, 100.0])
        self.assertTrue(manifest['watertight'])
        for filename in manifest['files'].values():
            self.assertTrue((scan.exports_dir / filename).exists())

    def test_3mf_declares_millimeter_units(self):
        scan = self.complete_with_obj()
        with zipfile.ZipFile(scan.exports_dir / 'model.3mf') as zf:
            model_xml = zf.read('3D/3dmodel.model').decode()
        self.assertIn('unit="millimeter"', model_xml)

    def test_stl_is_millimeter_scaled(self):
        import trimesh
        scan = self.complete_with_obj()
        mesh = trimesh.load(scan.exports_dir / 'model-mm.stl')
        for extent in mesh.extents:
            self.assertAlmostEqual(float(extent), 100.0, places=3)

    def test_glb_stays_in_meters(self):
        import trimesh
        scan = self.complete_with_obj()
        mesh = trimesh.load(scan.exports_dir / 'model.glb', force='mesh')
        for extent in mesh.extents:
            self.assertAlmostEqual(float(extent), 0.1, places=5)

    def test_complete_without_obj_still_works(self):
        self.client.put(
            '/api/scans/upload',
            data=make_bundle(),
            content_type='application/zip',
            headers={'X-Scan-Id': 'scan-no-obj'},
        )
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'fake-usdz')},
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id=job['scan_id'])
        self.assertEqual(scan.status, Scan.Status.COMPLETE)
        self.assertIsNone(scan.exports_manifest())

    def test_corrupt_obj_does_not_fail_completion(self):
        self.client.put(
            '/api/scans/upload',
            data=make_bundle(),
            content_type='application/zip',
            headers={'X-Scan-Id': 'scan-bad-obj'},
        )
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        bad_zip = io.BytesIO()
        with zipfile.ZipFile(bad_zip, 'w') as zf:
            zf.writestr('model.obj', 'not a valid obj at all\x00')
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'fake-usdz'), 'obj_zip': io.BytesIO(bad_zip.getvalue())},
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id=job['scan_id'])
        self.assertEqual(scan.status, Scan.Status.COMPLETE)
        self.assertIn('[exports] failed', scan.worker_log)

    def test_open_mesh_is_hole_filled_for_printing(self):
        # A cube missing two faces — like every real photogrammetry scan, open.
        open_obj = '\n'.join(CUBE_OBJ.strip().splitlines()[:-2]) + '\n'
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as zf:
            zf.writestr('model.obj', open_obj)
        self.client.put(
            '/api/scans/upload', data=make_bundle(), content_type='application/zip',
            headers={'X-Scan-Id': 'scan-open-mesh'},
        )
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        response = self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'fake'), 'obj_zip': io.BytesIO(buffer.getvalue())},
            headers=WORKER_HEADERS,
        )
        self.assertEqual(response.status_code, 200)
        scan = Scan.objects.get(scan_id='scan-open-mesh')
        manifest = scan.exports_manifest()
        self.assertIsNotNone(manifest, f'exports failed: {scan.worker_log}')

        # The print exports must be a hole-filled watertight solid.
        self.assertTrue(manifest['print_watertight'])
        self.assertIsNotNone(manifest['volume_cm3'])

        # Verify the actual STL on disk is watertight, not just the flag.
        import trimesh
        stl = trimesh.load(scan.exports_dir / 'model-mm.stl')
        self.assertTrue(stl.is_watertight)

    def test_reprocess_clears_stale_exports(self):
        scan = self.complete_with_obj()
        self.assertIsNotNone(scan.exports_manifest())
        # Requeue and complete again WITHOUT an obj zip (e.g. an older worker).
        self.client.post(f'/api/worker/{scan.scan_id}/requeue', headers=WORKER_HEADERS)
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'new-usdz')},
            headers=WORKER_HEADERS,
        )
        scan = Scan.objects.get(scan_id=scan.scan_id)
        # Old exports must not survive to describe the new reconstruction.
        self.assertIsNone(scan.exports_manifest())
        self.assertIsNone(scan.glb_url)

    def test_failed_regeneration_clears_stale_exports(self):
        scan = self.complete_with_obj()
        self.client.post(f'/api/worker/{scan.scan_id}/requeue', headers=WORKER_HEADERS)
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        bad_zip = io.BytesIO()
        with zipfile.ZipFile(bad_zip, 'w') as zf:
            zf.writestr('model.obj', 'garbage')
        self.client.post(
            f"/api/worker/{job['scan_id']}/complete",
            data={'usdz': io.BytesIO(b'new-usdz'), 'obj_zip': io.BytesIO(bad_zip.getvalue())},
            headers=WORKER_HEADERS,
        )
        scan = Scan.objects.get(scan_id=scan.scan_id)
        self.assertIsNone(scan.exports_manifest())

    def test_viewer_page(self):
        scan = self.complete_with_obj()
        response = self.client.get(f'/scans/{scan.scan_id}/viewer/')
        self.assertContains(response, 'model.glb')
        # Scan without exports -> 404
        self.client.put(
            '/api/scans/upload', data=make_bundle(), content_type='application/zip',
            headers={'X-Scan-Id': 'scan-no-viewer'},
        )
        self.assertEqual(self.client.get('/scans/scan-no-viewer/viewer/').status_code, 404)


@override_settings(MEDIA_ROOT=MEDIA_TMP, WORKER_TOKEN='test-worker-token', UPLOAD_TOKEN='')
class PhotoUploadTests(TestCase):
    def photo(self, name='IMG_0001.JPG', color=(120, 60, 30)):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (48, 48), color).save(buffer, 'JPEG')
        return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/jpeg')

    def test_page_renders(self):
        self.assertContains(self.client.get('/upload/'), 'Drop photos here')

    def test_upload_creates_queued_scan_with_worker_ready_bundle(self):
        photos = [self.photo(f'IMG_{i:04d}.JPG', (i * 20, 60, 30)) for i in range(3)]
        response = self.client.post('/upload/', {'photos': photos})
        self.assertEqual(response.status_code, 302)

        scan = Scan.objects.get(capture_mode='photos')
        self.assertEqual(scan.status, Scan.Status.QUEUED)
        self.assertEqual(scan.image_count, 3)
        self.assertTrue(scan.bundle_path.exists())

        # The bundle must match the layout the worker expects.
        with zipfile.ZipFile(scan.bundle_path) as zf:
            names = zf.namelist()
        self.assertIn('metadata.json', names)
        self.assertEqual(sum(1 for n in names if n.startswith('Images/')), 3)

        # And the worker must be able to claim and download it.
        job = self.client.post('/api/worker/claim', headers=WORKER_HEADERS).json()['job']
        self.assertEqual(job['scan_id'], scan.scan_id)
        bundle = self.client.get(job['bundle_url'], headers=WORKER_HEADERS)
        self.assertEqual(bundle.status_code, 200)

    def test_ajax_upload_returns_redirect_json(self):
        response = self.client.post(
            '/upload/', {'photos': [self.photo()]},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('redirect', response.json())

    def test_rejects_upload_with_no_images(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post(
            '/upload/', {'photos': [SimpleUploadedFile('notes.txt', b'hello', 'text/plain')]},
            headers={'X-Requested-With': 'XMLHttpRequest'},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Scan.objects.exists())

    def test_non_image_files_are_filtered_out(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.post('/upload/', {'photos': [
            self.photo('a.jpg'),
            SimpleUploadedFile('readme.txt', b'x', 'text/plain'),
            self.photo('b.jpg'),
        ]})
        self.assertEqual(Scan.objects.get().image_count, 2)

    def test_malicious_filenames_are_sanitised(self):
        self.client.post('/upload/', {'photos': [
            self.photo('../../../etc/passwd.jpg'),
            self.photo('..\\..\\windows.jpg'),
        ]})
        scan = Scan.objects.get()
        with zipfile.ZipFile(scan.bundle_path) as zf:
            names = zf.namelist()
        for name in names:
            self.assertNotIn('..', name)
        self.assertEqual(scan.image_count, 2)
        # Files land inside the scan directory, nowhere else.
        self.assertTrue((scan.extracted_dir / 'Images').is_dir())


@override_settings(MEDIA_ROOT=MEDIA_TMP, WORKER_TOKEN='test-worker-token', UPLOAD_TOKEN='')
class UITests(TestCase):
    def test_list_and_detail_render(self):
        self.client.put(
            '/api/scans/upload',
            data=make_bundle(),
            content_type='application/zip',
            headers={'X-Scan-Id': 'scan-20260722-120000-abcd'},
        )
        self.assertContains(self.client.get('/'), 'scan-20260722-120000-abcd')
        self.assertContains(self.client.get('/scans/scan-20260722-120000-abcd/'), 'Queued')
