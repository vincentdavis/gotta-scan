"""Browser photo upload: turn a set of loose photos into a standard scan bundle.

The worker always fetches `bundle.zip` for a job, so photos uploaded from the
browser are packaged into exactly the same bundle layout the iOS app produces
(Images/ + metadata.json) and then run through the normal ingest path.
"""

import json
import re
import uuid
import zipfile
from pathlib import Path

from django.utils import timezone

from .services import IMAGE_EXTENSIONS

MIN_PHOTOS = 10


def new_scan_id() -> str:
    """Same shape as the iOS app's IDs: ASCII, sortable, collision-proof."""
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    return f'scan-{stamp}-{uuid.uuid4().hex[:4]}'


def safe_photo_name(name: str, index: int) -> str:
    """Strip any path components and unsafe characters from a browser filename.

    The index prefix guarantees uniqueness and a stable capture order, which is
    what the reconstruction sees since images are processed in sorted order.
    """
    base = Path(str(name)).name
    base = re.sub(r'[^A-Za-z0-9._-]', '_', base).lstrip('.')
    if not base:
        base = 'photo.jpg'
    return f'{index:04d}_{base}'


def is_photo(filename: str) -> bool:
    return Path(str(filename)).suffix.lower() in IMAGE_EXTENSIONS


def write_photo_bundle(scan, photos) -> None:
    """Package uploaded photo files into scan.bundle_path.

    Photos are already compressed, so store them without re-deflating.
    """
    scan.scan_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        'name': scan.scan_id,
        'captureMode': 'photos',
        'source': 'web-upload',
        'imageCount': len(photos),
        'createdAt': timezone.now().isoformat(),
    }
    with zipfile.ZipFile(scan.bundle_path, 'w', zipfile.ZIP_STORED) as zf:
        zf.writestr('metadata.json', json.dumps(metadata, indent=2))
        for index, upload in enumerate(photos):
            entry = f'Images/{safe_photo_name(upload.name, index)}'
            with zf.open(entry, 'w') as dest:
                for chunk in upload.chunks():
                    dest.write(chunk)
