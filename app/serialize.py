from __future__ import annotations

from datetime import datetime

from app.models import Event, Job, Repair, RepairOrder, StoryVersion, Transcript, Upload


def iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def upload_out(u: Upload) -> dict:
    return {
        "id": str(u.id),
        "content_hash": u.content_hash,
        "s3_uri": f"s3://{u.s3_bucket}/{u.s3_key}",
        "content_type": u.content_type,
        "byte_size": u.byte_size,
        "media_kind": u.media_kind,
        "metadata": u.meta or {},
    }


def repair_out(
    repair: Repair,
    *,
    upload: Upload | None = None,
    evidence: list[Upload] | None = None,
    ro: RepairOrder | None = None,
    story: StoryVersion | None = None,
    transcript: Transcript | None = None,
    last_job: Job | None = None,
    duplicate: bool = False,
) -> dict:
    payload = story.payload if story else None
    latency_ms = None
    if repair.ingest_started_at and repair.ingest_finished_at:
        latency_ms = max(
            1, int((repair.ingest_finished_at - repair.ingest_started_at).total_seconds() * 1000)
        )
    last_error = None
    if last_job and last_job.last_error:
        last_error = last_job.last_error.splitlines()[0]
    return {
        "id": str(repair.id),
        "rooftop_id": repair.rooftop_id,
        "ro_number": repair.ro_number,
        "vin": repair.vin,
        "status": repair.status,
        "duplicate": duplicate,
        "created_at": iso(repair.created_at),
        "updated_at": iso(repair.updated_at),
        "filed_at": iso(repair.filed_at),
        "latency_ms": latency_ms,
        "last_error": last_error,
        "job_attempts": last_job.attempts if last_job else 0,
        "upload": upload_out(upload) if upload else None,
        "evidence": [upload_out(e) for e in (evidence or [])],
        "repair_order": {
            "labor_op": ro.labor_op,
            "labor_op_name": ro.labor_op_name,
            "vehicle": f"{ro.vehicle_year} {ro.vehicle_make} {ro.vehicle_model}",
            "mileage": ro.mileage,
            "advisor": ro.advisor,
            "technician": ro.technician,
            "customer_concern": ro.customer_concern,
        }
        if ro
        else None,
        "transcript": transcript.text if transcript else None,
        "transcript_model": transcript.model if transcript else None,
        "story": payload,
        "story_version": story.version if story else None,
        "schema_version": story.schema_version if story else None,
        "prompt_version": story.prompt_version if story else None,
        "validator_version": story.validator_version if story else None,
        "missing_required": list(story.missing_required) if story else [],
        "followup_questions": story.followup_questions if story else [],
    }


def event_out(ev: Event) -> dict:
    return {
        "id": ev.id,
        "ts": iso(ev.ts),
        "level": ev.level,
        "stage": ev.stage,
        "message": ev.message,
        "data": ev.data,
    }
