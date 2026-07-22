import io
import json
import tempfile
import zipfile
from pathlib import Path

from django.test import TestCase, override_settings

from .models import Scan

MEDIA_TMP = tempfile.mkdtemp(prefix='gottascan-test-media-')
WORKER_HEADERS = {'X-Worker-Token': 'test-worker-token'}


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
