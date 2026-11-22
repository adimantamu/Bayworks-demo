from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import dms, extract, jobs, s3util, transcribe
from app.events import emit
from app.models import DmsWriteback, Repair, RepairOrder, StoryVersion, Transcript, Upload, new_id
from app.validate import questions_for, validate, validator_meta
from app.warranty import PROMPT_VERSION, SCHEMA_VERSION, WarrantyStory


def latest_story(db: Session, repair_id: uuid.UUID) -> StoryVersion | None:
    return db.scalars(
        select(StoryVersion)
        .where(StoryVersion.repair_id == repair_id)
        .order_by(StoryVersion.version.desc())
    ).first()


def latest_ro(db: Session, rooftop_id: str, ro_number: str) -> RepairOrder | None:
    return db.scalars(
        select(RepairOrder).where(
            RepairOrder.rooftop_id == rooftop_id,
            RepairOrder.ro_number == ro_number,
        )
    ).first()


def set_status(db: Session, repair: Repair, status: str) -> None:
    repair.status = status
    repair.updated_at = datetime.now(timezone.utc)
    if repair.ingest_finished_at is None and status in {"filed", "needs_followup", "dead_letter"}:
        repair.ingest_finished_at = datetime.now(timezone.utc)
    if status == "filed":
        repair.filed_at = datetime.now(timezone.utc)


def process_job(db: Session, job) -> None:
    if job.job_type in {"ingest", "followup"}:
        process_ingest(db, job)
    elif job.job_type == "dms_sync":
        process_dms_sync(db, job)
    else:
        raise RuntimeError(f"unknown job type {job.job_type}")


def process_ingest(db: Session, job) -> None:
    upload = db.get(Upload, job.upload_id)
    repair = db.get(Repair, job.repair_id)
    if not upload or not repair:
        raise RuntimeError("upload or repair missing")

    if repair.ingest_started_at is None:
        repair.ingest_started_at = datetime.now(timezone.utc)

    emit(
        db,
        repair_id=repair.id,
        upload_id=upload.id,
        job_id=job.id,
        stage="ingest",
        message="Claimed job",
        data={"job_type": job.job_type, "attempt": job.attempts, "sha256": upload.content_hash},
    )

    set_status(db, repair, "transcribing")
    emit(db, repair_id=repair.id, stage="transcribe", message="Downloading object from object store")
    payload = s3util.get_bytes(upload.s3_key)
    suffix = upload.s3_key.split("/")[-1]
    text, model = transcribe.transcribe_bytes(payload, upload.content_type, suffix)
    trow = Transcript(
        id=new_id(),
        repair_id=repair.id,
        upload_id=upload.id,
        text=text,
        model=model,
    )
    db.add(trow)
    db.flush()
    emit(
        db,
        repair_id=repair.id,
        stage="transcribe",
        message="Transcript ready",
        data={"model": model, "chars": len(text)},
    )

    ro = latest_ro(db, repair.rooftop_id, repair.ro_number)
    set_status(db, repair, "extracting")
    emit(db, repair_id=repair.id, stage="extract", message="Filling warranty schema")
    story, extract_model = extract.extract_story(
        text,
        labor_op_name=ro.labor_op_name if ro else None,
        customer_concern=ro.customer_concern if ro else None,
    )

    if job.job_type == "followup":
        prev = latest_story(db, repair.id)
        if prev:
            story = extract.merge_stories(WarrantyStory.model_validate(prev.payload), story)

    set_status(db, repair, "validating")
    emit(db, repair_id=repair.id, stage="validate", message="Running warranty rules")
    story = validate(
        story,
        labor_op_name=ro.labor_op_name if ro else None,
        transcript=text,
    )
    qs = [q.model_dump() for q in questions_for(story.missing_required)]
    prev = latest_story(db, repair.id)
    version = (prev.version + 1) if prev else 1
    row = StoryVersion(
        id=new_id(),
        repair_id=repair.id,
        version=version,
        schema_version=SCHEMA_VERSION,
        prompt_version=story.prompt_version or PROMPT_VERSION,
        validator_version=validator_meta(),
        payload=json.loads(story.model_dump_json()),
        missing_required=story.missing_required,
        followup_questions=qs,
        source_transcript_id=trow.id,
    )
    db.add(row)
    db.flush()
    emit(
        db,
        repair_id=repair.id,
        stage="validate",
        message="Validation complete",
        data={
            "version": version,
            "missing": story.missing_required,
            "extract_model": extract_model,
            "repair_type": story.repair_type,
        },
    )

    if story.missing_required:
        set_status(db, repair, "needs_followup")
        emit(
            db,
            repair_id=repair.id,
            stage="quality",
            level="warn",
            message="Blocked before DMS file — required warranty fields missing",
            data={"missing": story.missing_required},
        )
        return

    set_status(db, repair, "filing")
    jobs.enqueue(db, job_type="dms_sync", repair_id=repair.id)
    emit(db, repair_id=repair.id, stage="dms", message="Enqueued DMS writeback")


def process_dms_sync(db: Session, job) -> None:
    repair = db.get(Repair, job.repair_id)
    if not repair:
        raise RuntimeError("repair missing")
    story_row = latest_story(db, repair.id)
    if not story_row:
        raise RuntimeError("no story to file")
    upload = db.get(Upload, repair.upload_id)
    evidence_rows = db.scalars(
        select(Upload).where(Upload.repair_id == repair.id, Upload.media_kind != "audio")
    ).all()
    key = f"{repair.rooftop_id}:{repair.ro_number}:{upload.content_hash if upload else repair.id}:v{story_row.version}"
    existing = db.scalars(select(DmsWriteback).where(DmsWriteback.idempotency_key == key)).first()
    if existing and existing.status == "succeeded":
        set_status(db, repair, "filed")
        emit(db, repair_id=repair.id, stage="dms", message="Writeback already succeeded (idempotent)")
        return

    payload = {
        "ro_number": repair.ro_number,
        "vin": repair.vin,
        "rooftop_id": repair.rooftop_id,
        "story": story_row.payload,
        "audio_sha256": upload.content_hash if upload else None,
        "s3_uri": f"s3://{upload.s3_bucket}/{upload.s3_key}" if upload else None,
        "evidence_uris": [f"s3://{e.s3_bucket}/{e.s3_key}" for e in evidence_rows],
        "schema_version": story_row.schema_version,
        "prompt_version": story_row.prompt_version,
        "validator_version": story_row.validator_version,
        "story_version": story_row.version,
    }
    wb = existing or DmsWriteback(
        id=new_id(),
        repair_id=repair.id,
        idempotency_key=key,
        request_body=payload,
        status="pending",
        attempts=0,
    )
    wb.attempts = (wb.attempts or 0) + 1
    db.add(wb)
    db.flush()
    emit(
        db,
        repair_id=repair.id,
        stage="dms",
        message="Pushing warranty story to DMS",
        data={"attempt": wb.attempts, "idempotency_key": key},
    )
    try:
        status, body = dms.push_story(repair.ro_number, payload, key)
    except dms.DmsError as exc:
        wb.status = "failed"
        wb.response_status = exc.status
        wb.response_body = exc.body
        wb.updated_at = datetime.now(timezone.utc)
        db.commit()
        raise
    wb.status = "succeeded"
    wb.response_status = status
    wb.response_body = body
    wb.updated_at = datetime.now(timezone.utc)
    set_status(db, repair, "filed")
    emit(
        db,
        repair_id=repair.id,
        stage="dms",
        message="Filed to DMS",
        data={"dms_story_id": body.get("story_id"), "status": status},
    )
