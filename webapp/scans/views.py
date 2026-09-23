import logging
from urllib.parse import quote

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .models import Scan
from .services import BundleError, ingest_bundle
from .uploads import MIN_PHOTOS, is_photo, new_scan_id, write_photo_bundle

logger = logging.getLogger(__name__)


def scan_list(request):
    return render(request, 'scans/scan_list.html', {'scans': Scan.objects.all()})


def scan_detail(request, scan_id):
    scan = get_object_or_404(Scan, scan_id=scan_id)
    return render(request, 'scans/scan_detail.html', {
        'scan': scan,
        'foot_error': request.GET.get('foot_error'),
    })


def analyze_foot(request, scan_id):
    """Run foot-measurement analysis on a scan's mesh (POST), then show the detail page."""
    from .analysis.foot import FootMeasurementError, measure_foot_for_scan

    scan = get_object_or_404(Scan, scan_id=scan_id)
    detail = reverse('scans:detail', args=[scan.scan_id])
    if request.method != 'POST':
        return redirect(detail)
    try:
        measure_foot_for_scan(scan)
    except FootMeasurementError as exc:
        return redirect(f'{detail}?foot_error={quote(str(exc))}')
    except Exception:
        logger.exception('Foot analysis failed for %s', scan.scan_id)
        return redirect(f'{detail}?foot_error={quote("Internal error during analysis.")}')
    return redirect(detail)


def upload_photos(request):
    """Upload a set of loose photos from the browser as a new scan."""
    token_required = bool(settings.UPLOAD_TOKEN)
    if request.method != 'POST':
        return render(request, 'scans/upload.html', {
            'min_photos': MIN_PHOTOS,
            'token_required': token_required,
        })

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def fail(message, status=400):
        if wants_json:
            return JsonResponse({'error': message}, status=status)
        return render(
            request,
            'scans/upload.html',
            {'min_photos': MIN_PHOTOS, 'token_required': token_required, 'error': message},
            status=status,
        )

    # Same token gate as the iOS bundle upload, so a public server can't be
    # filled with anonymous photo uploads. Token via form field or header.
    if token_required:
        supplied = request.POST.get('upload_token', '') or request.headers.get('X-Upload-Token', '')
        if supplied != settings.UPLOAD_TOKEN:
            return fail('Invalid or missing upload token.', 403)

    photos = [f for f in request.FILES.getlist('photos') if is_photo(f.name)]
    if not photos:
        return fail('No image files found. Select JPEG, HEIC, or PNG photos.')
    if sum(f.size for f in photos) > settings.SCAN_MAX_UPLOAD_BYTES:
        return fail('That photo set is too large.', 413)

    scan = Scan.objects.create(scan_id=new_scan_id(), capture_mode='photos')
    try:
        write_photo_bundle(scan, photos)
        ingest_bundle(scan)
    except BundleError as exc:
        scan.status = Scan.Status.FAILED
        scan.error_message = str(exc)
        scan.save()
        return fail(str(exc), 422)
    except Exception:
        logger.exception('Photo upload failed for %s', scan.scan_id)
        scan.status = Scan.Status.FAILED
        scan.error_message = 'Internal error while processing photos.'
        scan.save()
        return fail('Internal error while processing photos.', 500)

    detail_url = reverse('scans:detail', args=[scan.scan_id])
    if wants_json:
        return JsonResponse({
            'scan_id': scan.scan_id,
            'image_count': scan.image_count,
            'redirect': detail_url,
        })
    return redirect(detail_url)


def scan_viewer(request, scan_id):
    scan = get_object_or_404(Scan, scan_id=scan_id)
    glb_url = scan.glb_url
    if glb_url is None:
        raise Http404('No web model generated for this scan yet.')
    return render(request, 'scans/viewer.html', {
        'scan': scan,
        'glb_url': glb_url,
        'manifest': scan.exports_manifest(),
    })
