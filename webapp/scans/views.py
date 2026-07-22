from django.shortcuts import get_object_or_404, render

from .models import Scan


def scan_list(request):
    return render(request, 'scans/scan_list.html', {'scans': Scan.objects.all()})


def scan_detail(request, scan_id):
    scan = get_object_or_404(Scan, scan_id=scan_id)
    return render(request, 'scans/scan_detail.html', {'scan': scan})
