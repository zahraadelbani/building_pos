import json

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .models import PrintJob


def _require_agent_headers(request):
    device_id = request.headers.get("X-Device-Id", "").strip()
    token = request.headers.get("X-Print-Agent-Token", "").strip()

    if not device_id:
        return None, JsonResponse({"error": "Missing X-Device-Id header."}, status=400)

    expected = (settings.PRINT_AGENT_TOKEN or "").strip()
    if not expected:
        return None, JsonResponse({"error": "Server PRINT_AGENT_TOKEN is not configured."}, status=500)
    if token != expected:
        return None, JsonResponse({"error": "Invalid print agent token."}, status=403)

    return device_id, None


@csrf_exempt
@require_GET
def poll_jobs(request):
    device_id, error_response = _require_agent_headers(request)
    if error_response:
        return error_response

    try:
        limit = int(request.GET.get("limit", "5"))
    except Exception:
        limit = 5
    limit = max(1, min(limit, 20))

    now = timezone.now()

    def _claim_queryset(queryset):
        job_ids = list(queryset.values_list("id", flat=True)[:limit])
        if not job_ids:
            return []

        PrintJob.objects.filter(id__in=job_ids, status=PrintJob.STATUS_PENDING).update(
            status=PrintJob.STATUS_CLAIMED,
            claimed_by_device_id=device_id,
            claimed_at=now,
        )
        return list(
            PrintJob.objects.filter(id__in=job_ids, status=PrintJob.STATUS_CLAIMED).values(
                "id",
                "job_type",
                "copy_type",
                "payload",
            )
        )

    with transaction.atomic():
        targeted = (
            PrintJob.objects.select_for_update()
            .filter(status=PrintJob.STATUS_PENDING, target_device_id=device_id)
            .order_by("-priority", "created_at")
        )
        jobs = _claim_queryset(targeted)

        if not jobs:
            untargeted = (
                PrintJob.objects.select_for_update()
                .filter(status=PrintJob.STATUS_PENDING, target_device_id__isnull=True)
                .order_by("-priority", "created_at")
            )
            jobs = _claim_queryset(untargeted)

    return JsonResponse({"jobs": jobs})


@csrf_exempt
@require_POST
def ack_printed(request, job_id: int):
    device_id, error_response = _require_agent_headers(request)
    if error_response:
        return error_response

    job = get_object_or_404(PrintJob, id=job_id)
    if job.status != PrintJob.STATUS_CLAIMED or job.claimed_by_device_id != device_id:
        return JsonResponse({"error": "Job is not claimed by this device."}, status=409)

    job.status = PrintJob.STATUS_PRINTED
    job.printed_at = timezone.now()
    job.save(update_fields=["status", "printed_at"])
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def ack_failed(request, job_id: int):
    device_id, error_response = _require_agent_headers(request)
    if error_response:
        return error_response

    job = get_object_or_404(PrintJob, id=job_id)
    if job.status != PrintJob.STATUS_CLAIMED or job.claimed_by_device_id != device_id:
        return JsonResponse({"error": "Job is not claimed by this device."}, status=409)

    try:
        body = json.loads((request.body or b"{}").decode("utf-8"))
    except Exception:
        body = {}

    error = (body.get("error") or "").strip()[:4000] or "Unknown error"
    job.attempts += 1
    job.last_error = error

    if job.attempts >= getattr(settings, "PRINT_JOB_MAX_ATTEMPTS", 3):
        job.status = PrintJob.STATUS_FAILED
        job.save(update_fields=["status", "attempts", "last_error"])
        return JsonResponse({"ok": True, "status": job.status})

    job.status = PrintJob.STATUS_PENDING
    job.claimed_by_device_id = None
    job.claimed_at = None
    job.save(update_fields=["status", "attempts", "last_error", "claimed_by_device_id", "claimed_at"])
    return JsonResponse({"ok": True, "status": job.status})

