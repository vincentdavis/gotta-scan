import logging

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
    return render(request, 'scans/scan_detail.html', {'scan': scan})


def upload_photos(request):
    """Upload a set of loose photos from the browser as a new scan."""
    if request.method != 'POST':
        return render(request, 'scans/upload.html', {'min_photos': MIN_PHOTOS})

    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def fail(message, status=400):
        if wants_json:
            return JsonResponse({'error': message}, status=status)
        return render(
            request,
            'scans/upload.html',
            {'min_photos': MIN_PHOTOS, 'error': message},
            status=status,
        )

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
