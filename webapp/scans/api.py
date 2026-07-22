"""JSON/binary API: scan upload from the iOS app, job queue for the Mac worker."""

import functools
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .exports import ExportError, generate_exports
from .models import Scan
from .services import BundleError, ingest_bundle, safe_extract

logger = logging.getLogger(__name__)


def _error(message: str, status: int) -> JsonResponse:
    return JsonResponse({'error': message}, status=status)


def worker_auth(view):
    """The Mac worker authenticates with a shared token header."""
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        token = request.headers.get('X-Worker-Token', '')
        if not settings.WORKER_TOKEN or token != settings.WORKER_TOKEN:
            return _error('Invalid worker token.', 403)
        return view(request, *args, **kwargs)
    return wrapped


@csrf_exempt
def upload_bundle(request):
    """PUT a scan bundle ZIP. Matches the iOS app: raw body, X-Scan-Id header."""
    if request.method != 'PUT':
        return HttpResponseNotAllowed(['PUT'])

    if settings.UPLOAD_TOKEN and request.headers.get('X-Upload-Token', '') != settings.UPLOAD_TOKEN:
        return _error('Invalid upload token.', 403)

    scan_id = request.headers.get('X-Scan-Id', '').strip()
    if not scan_id:
        return _error('Missing X-Scan-Id header.', 400)
    # scan_id becomes a directory name — keep it strictly filesystem-safe.
    if not all(c.isalnum() or c in '-_.' for c in scan_id) or scan_id.startswith('.') or len(scan_id) > 120:
        return _error('Invalid X-Scan-Id.', 400)

    declared = request.headers.get('Content-Length')
    if declared and int(declared) > settings.SCAN_MAX_UPLOAD_BYTES:
        return _error('Bundle too large.', 413)

    scan, created = Scan.objects.get_or_create(scan_id=scan_id)
    scan.scan_dir.mkdir(parents=True, exist_ok=True)

    def abort(message: str, status: int) -> JsonResponse:
        partial_path.unlink(missing_ok=True)
        # A fresh row must not linger in 'uploaded' (a status the queue never
        # reads); an existing scan keeps its previous bundle untouched.
        if created:
            scan.status = Scan.Status.FAILED
            scan.error_message = message
            scan.save()
        return _error(message, status)

    # Stream to a temp file so an interrupted upload can never clobber an
    # existing good bundle; promote with os.replace only on success.
    partial_path = scan.scan_dir / 'bundle.zip.part'
    written = 0
    try:
        with open(partial_path, 'wb') as out:
            while chunk := request.read(1024 * 1024):
                written += len(chunk)
                if written > settings.SCAN_MAX_UPLOAD_BYTES:
                    return abort('Bundle too large.', 413)
                out.write(chunk)
    except OSError:
        # Client disconnected mid-body (django.http.UnreadablePostError et al.).
        return abort('Upload interrupted.', 400)

    if written == 0:
        return abort('Empty upload.', 400)
    if declared and written != int(declared):
        return abort('Upload truncated.', 400)

    partial_path.replace(scan.bundle_path)
    scan.status = Scan.Status.UPLOADED
    scan.save()

    try:
        ingest_bundle(scan)
    except BundleError as exc:
        scan.status = Scan.Status.FAILED
        scan.error_message = str(exc)
        scan.save()
        return _error(str(exc), 422)
    except Exception:
        logger.exception('Ingest failed for %s', scan_id)
        scan.status = Scan.Status.FAILED
        scan.error_message = 'Internal error during ingest.'
        scan.save()
        return _error('Internal error during ingest.', 500)

    return JsonResponse({'scan_id': scan.scan_id, 'status': scan.status, 'image_count': scan.image_count})


@csrf_exempt
@worker_auth
def claim_job(request):
    """POST: atomically claim the oldest queued scan; job=null if none."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    # Reaper: a worker that died mid-job leaves scans in 'processing' forever;
    # anything claimed longer ago than the stale window goes back in the queue.
    stale_cutoff = timezone.now() - timedelta(seconds=settings.SCAN_STALE_CLAIM_SECONDS)
    Scan.objects.filter(status=Scan.Status.PROCESSING, claimed_at__lt=stale_cutoff).update(
        status=Scan.Status.QUEUED, claimed_at=None,
    )

    with transaction.atomic():
        scan = (
            Scan.objects.select_for_update(skip_locked=True)
            .filter(status=Scan.Status.QUEUED)
            .order_by('created_at')
            .first()
        )
        if scan is None:
            return JsonResponse({'job': None}, status=200)
        scan.status = Scan.Status.PROCESSING
        scan.claimed_at = timezone.now()
        scan.save()
    return JsonResponse({
        'job': {
            'scan_id': scan.scan_id,
            'image_count': scan.image_count,
            'bundle_url': reverse('scans:api-bundle', args=[scan.scan_id]),
        }
    })


@worker_auth
def download_bundle(request, scan_id):
    try:
        scan = Scan.objects.get(scan_id=scan_id)
    except Scan.DoesNotExist:
        return _error('Unknown scan.', 404)
    if not scan.bundle_path.exists():
        return _error('Bundle file missing.', 410)
    return FileResponse(open(scan.bundle_path, 'rb'), as_attachment=True, filename=f'{scan.scan_id}.zip')


@csrf_exempt
@worker_auth
def complete_job(request, scan_id):
    """POST multipart with the reconstructed model file (field: usdz)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        scan = Scan.objects.get(scan_id=scan_id)
    except Scan.DoesNotExist:
        return _error('Unknown scan.', 404)

    upload = request.FILES.get('usdz')
    if upload is None:
        return _error('Missing usdz file field.', 400)

    import shutil

    scan.models_dir.mkdir(parents=True, exist_ok=True)
    with open(scan.model_usdz_path, 'wb') as out:
        for chunk in upload.chunks():
            out.write(chunk)

    # This completion replaces the model entirely: stale exports from a previous
    # run must never be served against the new reconstruction.
    if scan.exports_dir.exists():
        shutil.rmtree(scan.exports_dir)

    worker_log = request.POST.get('log', '')[-20000:]

    # Optional OBJ master mesh (obj + mtl + textures, zipped by the worker).
    # Export generation is best-effort: a bad mesh must not fail the job.
    obj_zip = request.FILES.get('obj_zip')
    if obj_zip is not None:
        obj_zip_path = scan.models_dir / 'model-obj.zip'
        with open(obj_zip_path, 'wb') as out:
            for chunk in obj_zip.chunks():
                out.write(chunk)
        obj_dir = scan.models_dir / 'obj'
        try:
            if obj_dir.exists():
                shutil.rmtree(obj_dir)
            safe_extract(obj_zip_path, obj_dir, settings.SCAN_MAX_EXTRACTED_BYTES)
            manifest = generate_exports(scan)
            worker_log += (
                f"\n[exports] dimensions_mm={manifest['dimensions_mm']} "
                f"watertight={manifest['watertight']} faces={manifest['faces']}"
            )
        except (BundleError, ExportError) as exc:
            logger.warning('Export generation failed for %s: %s', scan.scan_id, exc)
            worker_log += f'\n[exports] failed: {exc}'
        except Exception:
            logger.exception('Export generation crashed for %s', scan.scan_id)
            worker_log += '\n[exports] failed: internal error'

    scan.status = Scan.Status.COMPLETE
    scan.completed_at = timezone.now()
    scan.worker_log = worker_log
    scan.error_message = ''
    scan.save()
    return JsonResponse({'scan_id': scan.scan_id, 'status': scan.status})


@csrf_exempt
@worker_auth
def fail_job(request, scan_id):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        scan = Scan.objects.get(scan_id=scan_id)
    except Scan.DoesNotExist:
        return _error('Unknown scan.', 404)
    # A late/duplicate failure report must not downgrade a scan whose model
    # already landed (e.g. complete POST timed out after the server saved it).
    if scan.status == Scan.Status.COMPLETE:
        return JsonResponse({'scan_id': scan.scan_id, 'status': scan.status, 'ignored': True})
    scan.status = Scan.Status.FAILED
    scan.error_message = request.POST.get('error', 'Worker reported failure.')[:2000]
    scan.worker_log = request.POST.get('log', '')[-20000:]
    scan.save()
    return JsonResponse({'scan_id': scan.scan_id, 'status': scan.status})


@csrf_exempt
@worker_auth
def requeue_job(request, scan_id):
    """POST: put a processing/failed scan back in the queue (worker crash recovery)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        scan = Scan.objects.get(scan_id=scan_id)
    except Scan.DoesNotExist:
        return _error('Unknown scan.', 404)
    scan.status = Scan.Status.QUEUED
    scan.claimed_at = None
    scan.error_message = ''
    scan.save()
    return JsonResponse({'scan_id': scan.scan_id, 'status': scan.status})
