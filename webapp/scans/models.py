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
