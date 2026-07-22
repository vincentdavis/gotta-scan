from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .models import Scan


def scan_list(request):
    return render(request, 'scans/scan_list.html', {'scans': Scan.objects.all()})


def scan_detail(request, scan_id):
    scan = get_object_or_404(Scan, scan_id=scan_id)
    return render(request, 'scans/scan_detail.html', {'scan': scan})


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
