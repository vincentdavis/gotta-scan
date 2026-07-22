"""Bundle ingest: safe extraction, metadata parsing, thumbnail generation."""

import json
import logging
import shutil
import zipfile
from pathlib import Path

from django.conf import settings

from .models import Scan

logger = logging.getLogger(__name__)

IMAGE_EXTENSIONS = {'.heic', '.heif', '.jpg', '.jpeg', '.png'}


class BundleError(Exception):
    """The uploaded bundle is malformed or unsafe."""


def _validate_member(name: str) -> bool:
    """Reject zip-slip: absolute paths or parent-directory traversal."""
    if name.startswith('/') or name.startswith('\\'):
        return False
    parts = Path(name.replace('\\', '/')).parts
    return '..' not in parts


def safe_extract(zip_path: Path, dest: Path, max_bytes: int) -> None:
    """Extract with zip-slip validation and a decompressed-size cap.

    The cap is enforced both on declared header sizes and on actual bytes
    written (headers can be forged), so a zip bomb can't fill the disk.
    """
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        declared_total = 0
        for member in members:
            if not _validate_member(member.filename):
                raise BundleError(f'Unsafe path in bundle: {member.filename!r}')
            declared_total += member.file_size
        if declared_total > max_bytes:
            raise BundleError('Bundle decompresses beyond the size limit.')

        dest.mkdir(parents=True, exist_ok=True)
        written = 0
        for member in members:
            target = dest / member.filename.replace('\\', '/')
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, 'wb') as out:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise BundleError('Bundle decompresses beyond the size limit.')
                    out.write(chunk)


def _find_bundle_root(extracted: Path) -> Path:
    """The iOS app zips the whole scan folder, so metadata.json sits one level down."""
    if (extracted / 'metadata.json').exists():
        return extracted
    for child in sorted(extracted.iterdir()):
        if child.is_dir() and not child.name.startswith('__MACOSX') and (child / 'metadata.json').exists():
            return child
    # No metadata.json anywhere — accept the first real directory, or the root.
    for child in sorted(extracted.iterdir()):
        if child.is_dir() and not child.name.startswith('__MACOSX'):
            return child
    return extracted


def find_images_dir(extracted: Path) -> Path | None:
    root = _find_bundle_root(extracted)
    images = root / 'Images'
    if images.is_dir():
        return images
    # Fall back to any directory that actually contains images.
    candidates = [root] + [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    for candidate in candidates:
        if any(p.suffix.lower() in IMAGE_EXTENSIONS for p in candidate.iterdir() if p.is_file()):
            return candidate
    return None


def list_images(extracted: Path) -> list[Path]:
    images_dir = find_images_dir(extracted)
    if images_dir is None:
        return []
    return sorted(
        p for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _generate_thumbnails(images: list[Path], thumbs_dir: Path, count: int) -> None:
    try:
        from PIL import Image
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        logger.warning('Pillow/pillow-heif unavailable; skipping thumbnails')
        return

    thumbs_dir.mkdir(parents=True, exist_ok=True)
    step = max(1, len(images) // count) if images else 1
    for index, image_path in enumerate(images[::step][:count]):
        try:
            with Image.open(image_path) as im:
                im.thumbnail((480, 480))
                im.convert('RGB').save(thumbs_dir / f'thumb_{index:02d}.jpg', quality=80)
        except Exception:
            logger.exception('Thumbnail failed for %s', image_path.name)


def ingest_bundle(scan: Scan) -> None:
    """Extract an uploaded bundle.zip, read its metadata, and queue it for processing."""
    # A re-upload replaces the capture entirely: clear every derived artifact so
    # a stale model.usdz or leftover thumbnails can't masquerade as the new scan's.
    for stale_dir in (scan.extracted_dir, scan.thumbs_dir, scan.models_dir):
        if stale_dir.exists():
            shutil.rmtree(stale_dir)
    try:
        safe_extract(scan.bundle_path, scan.extracted_dir, settings.SCAN_MAX_EXTRACTED_BYTES)
    except BundleError:
        # Reclaim whatever was partially extracted before the cap tripped.
        if scan.extracted_dir.exists():
            shutil.rmtree(scan.extracted_dir)
        raise

    root = _find_bundle_root(scan.extracted_dir)
    metadata = {}
    metadata_path = root / 'metadata.json'
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning('Unreadable metadata.json for %s', scan.scan_id)

    images = list_images(scan.extracted_dir)
    if not images:
        raise BundleError('Bundle contains no images.')

    _generate_thumbnails(images, scan.thumbs_dir, settings.SCAN_THUMBNAIL_COUNT)

    scan.image_count = len(images)
    scan.capture_mode = str(metadata.get('captureMode', ''))[:30]
    scan.device_metadata = metadata
    scan.status = Scan.Status.QUEUED
    scan.error_message = ''
    scan.worker_log = ''
    scan.completed_at = None
    scan.claimed_at = None
    scan.save()
