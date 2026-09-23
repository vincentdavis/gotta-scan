from pathlib import Path

from django.conf import settings
from django.db import models


class Scan(models.Model):
    """One uploaded capture bundle from the GOTTA.SCAN iOS app."""

    class Status(models.TextChoices):
        UPLOADED = 'uploaded', 'Uploaded'
        QUEUED = 'queued', 'Queued'
        PROCESSING = 'processing', 'Processing'
        COMPLETE = 'complete', 'Complete'
        FAILED = 'failed', 'Failed'

    scan_id = models.CharField(max_length=120, unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.UPLOADED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    image_count = models.IntegerField(default=0)
    capture_mode = models.CharField(max_length=30, blank=True)
    device_metadata = models.JSONField(default=dict, blank=True)

    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    worker_log = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.scan_id} ({self.status})'

    # --- Filesystem layout: MEDIA_ROOT/scans/<scan_id>/ ---

    @property
    def scan_dir(self) -> Path:
        return Path(settings.MEDIA_ROOT) / 'scans' / self.scan_id

    @property
    def bundle_path(self) -> Path:
        return self.scan_dir / 'bundle.zip'

    @property
    def extracted_dir(self) -> Path:
        return self.scan_dir / 'extracted'

    @property
    def thumbs_dir(self) -> Path:
        return self.scan_dir / 'thumbs'

    @property
    def models_dir(self) -> Path:
        return self.scan_dir / 'models'

    @property
    def model_usdz_path(self) -> Path:
        return self.models_dir / 'model.usdz'

    @property
    def has_model(self) -> bool:
        return self.model_usdz_path.exists()

    @property
    def exports_dir(self) -> Path:
        return self.models_dir / 'exports'

    @property
    def obj_dir(self) -> Path:
        return self.models_dir / 'obj'

    @property
    def has_obj_mesh(self) -> bool:
        return self.obj_dir.is_dir() and any(self.obj_dir.glob('*.obj'))

    def foot_measurements(self) -> dict | None:
        path = self.models_dir / 'foot_measurements.json'
        if not path.exists():
            return None
        import json
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def exports_manifest(self) -> dict | None:
        manifest_path = self.exports_dir / 'exports.json'
        if not manifest_path.exists():
            return None
        import json
        try:
            return json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def export_downloads(self) -> list[dict]:
        """(label, url, note) for each generated export, for the detail template."""
        manifest = self.exports_manifest()
        if not manifest:
            return []
        base = f'{settings.MEDIA_URL}scans/{self.scan_id}/models/exports/'
        labels = {
            '3mf': ('3MF (print — recommended)', 'millimeters, open in PrusaSlicer/Bambu/Orca/Cura'),
            'stl': ('STL (print — legacy)', 'pre-scaled to millimeters'),
            'onshape_obj': ('OBJ for OnShape', 'decimated, millimeters — pick "millimeter" at import'),
            'glb': ('GLB (web/Blender)', 'meters, textured'),
        }
        downloads = []
        for key, filename in manifest.get('files', {}).items():
            label, note = labels.get(key, (key, ''))
            downloads.append({'label': label, 'url': base + filename, 'note': note})
        return downloads

    @property
    def glb_url(self) -> str | None:
        manifest = self.exports_manifest()
        if manifest and 'glb' in manifest.get('files', {}):
            return f"{settings.MEDIA_URL}scans/{self.scan_id}/models/exports/{manifest['files']['glb']}"
        return None

    def thumb_urls(self) -> list[str]:
        if not self.thumbs_dir.is_dir():
            return []
        return [
            f'{settings.MEDIA_URL}scans/{self.scan_id}/thumbs/{p.name}'
            for p in sorted(self.thumbs_dir.glob('*.jpg'))
        ]

    @property
    def model_url(self) -> str:
        return f'{settings.MEDIA_URL}scans/{self.scan_id}/models/model.usdz'
