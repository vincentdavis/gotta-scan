from django.urls import path

from . import api, views

app_name = 'scans'

urlpatterns = [
    path('', views.scan_list, name='list'),
    path('scans/<str:scan_id>/', views.scan_detail, name='detail'),
    path('scans/<str:scan_id>/viewer/', views.scan_viewer, name='viewer'),

    # iOS app upload
    path('api/scans/upload', api.upload_bundle, name='api-upload'),

    # Mac worker job queue
    path('api/worker/claim', api.claim_job, name='api-claim'),
    path('api/scans/<str:scan_id>/bundle', api.download_bundle, name='api-bundle'),
    path('api/worker/<str:scan_id>/complete', api.complete_job, name='api-complete'),
    path('api/worker/<str:scan_id>/fail', api.fail_job, name='api-fail'),
    path('api/worker/<str:scan_id>/requeue', api.requeue_job, name='api-requeue'),
]
