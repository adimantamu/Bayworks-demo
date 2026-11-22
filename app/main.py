from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import dms, jobs, s3util
from app.config import settings
from app.db import get_db, init_db
from app.events import emit
from app.extract import apply_answers
from app.models import Event, Job, Repair, RepairOrder, StoryVersion, Transcript, Upload, new_id
from app.pipeline import latest_ro, latest_story
from app.seed import ROOFTOP, ensure_rooftop, seed, seed_dummy_media
from app.serialize import event_out, repair_out
from app.validate import questions_for, validate, validator_meta
from app.warranty import PROMPT_VERSION, SCHEMA_VERSION, WarrantyStory

STATIC = Path(__file__).resolve().parent / "static"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"

app = FastAPI(title="Bayworks bay-to-ground-truth", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.on_event("startup")
def startup() -> None:
    init_db()
    s3util.ensure_bucket()
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        seed(db)
        seed_dummy_media(db)
    finally:
        db.close()


@app.get("/")
def landing_page():
    return FileResponse(STATIC / "index.html")


@app.get("/bay")
def tech_page():
    return FileResponse(STATIC / "tech.html")


@app.get("/ops")
def ops_page():
    return FileResponse(STATIC / "ops.html")


@app.get("/api/health")
def health():
    return {"ok": True}


class PresignIn(BaseModel):
    rooftop_id: str
    ro_number: str
    content_hash: str
    content_type: str
    byte_size: int | None = None
    media_kind: str = "audio"


class CompleteIn(BaseModel):
    rooftop_id: str
    ro_number: str
    vin: str | None = None
    content_hash: str
    s3_key: str
    content_type: str
    byte_size: int | None = None
    parent_repair_id: str | None = None


class EvidenceIn(BaseModel):
    content_hash: str
    s3_key: str
    content_type: str
    byte_size: int | None = None
    media_kind: str = "video"
    metadata: dict = Field(default_factory=dict)


class ScenarioIn(BaseModel):
    scenario: str
    rooftop_id: str = "keegan-honda"


class AnswersIn(BaseModel):
    answers: dict[str, str]


class DmsFailIn(BaseModel):
    count: int = 1


SCENARIOS = {
    "brake_complete": ("24-1009", "brake_complete.txt"),
    "brake_incomplete": ("24-1009", "brake_incomplete.txt"),
    "misfire_complete": ("24-1088", "misfire_complete.txt"),
    "misfire_incomplete": ("24-1088", "misfire_incomplete.txt"),
    "battery_complete": ("24-1102", "battery_complete.txt"),
}


def _hydrate(db: Session, repair: Repair, duplicate: bool = False) -> dict:
    upload = db.get(Upload, repair.upload_id)
    evidence = db.scalars(
        select(Upload)
        .where(Upload.repair_id == repair.id, Upload.id != repair.upload_id)
        .order_by(Upload.created_at)
    ).all()
    ro = latest_ro(db, repair.rooftop_id, repair.ro_number)
    story = latest_story(db, repair.id)
    transcript = db.scalars(
        select(Transcript).where(Transcript.repair_id == repair.id).order_by(Transcript.created_at.desc())
    ).first()
    last_job = db.scalars(
        select(Job).where(Job.repair_id == repair.id).order_by(Job.created_at.desc())
    ).first()
    return repair_out(
        repair,
        upload=upload,
        evidence=list(evidence),
        ro=ro,
        story=story,
        transcript=transcript,
        last_job=last_job,
        duplicate=duplicate,
    )


@app.get("/api/repair-orders")
def list_ros(rooftop_id: str = ROOFTOP["id"], db: Session = Depends(get_db)):
    ensure_rooftop(db, rooftop_id)
    db.commit()
    rows = db.scalars(
        select(RepairOrder)
        .where(RepairOrder.rooftop_id == rooftop_id)
        .order_by(RepairOrder.ro_number)
    ).all()
    return {
        "rooftop_id": rooftop_id,
        "orders": [
            {
                "ro_number": r.ro_number,
                "vin": r.vin,
                "vehicle": f"{r.vehicle_year} {r.vehicle_make} {r.vehicle_model}",
                "labor_op_name": r.labor_op_name,
                "mileage": r.mileage,
                "customer_concern": r.customer_concern,
                "technician": r.technician,
            }
            for r in rows
        ],
    }


@app.get("/api/repair-orders/{ro_number}")
def get_ro(ro_number: str, rooftop_id: str = ROOFTOP["id"], db: Session = Depends(get_db)):
    try:
        remote = dms.fetch_repair_order(ro_number)
    except Exception as exc:
        local = latest_ro(db, rooftop_id, ro_number)
        if not local:
            raise HTTPException(404, f"RO not in DMS: {exc}") from exc
        remote = {
            "ro_number": local.ro_number,
            "vin": local.vin,
            "labor_op_name": local.labor_op_name,
            "vehicle_year": local.vehicle_year,
            "vehicle_make": local.vehicle_make,
            "vehicle_model": local.vehicle_model,
            "mileage": local.mileage,
            "customer_concern": local.customer_concern,
            "technician": local.technician,
            "advisor": local.advisor,
        }
    return remote


@app.post("/api/uploads/presign")
def presign(body: PresignIn, db: Session = Depends(get_db)):
    rooftop_id = ensure_rooftop(db, body.rooftop_id).id
    existing = db.scalars(
        select(Upload).where(
            Upload.rooftop_id == rooftop_id,
            Upload.content_hash == body.content_hash,
        )
    ).first()
    if existing:
        repair = None
        if existing.repair_id:
            repair = db.get(Repair, existing.repair_id)
        if not repair:
            repair = db.scalars(select(Repair).where(Repair.upload_id == existing.id)).first()
        if repair:
            emit(
                db,
                repair_id=repair.id,
                upload_id=existing.id,
                stage="ingest",
                message="Idempotent replay — same content hash, presign skipped",
                data={"sha256": body.content_hash},
            )
            db.commit()
        return {
            "idempotent": True,
            "s3_key": existing.s3_key,
            "upload_url": None,
            "repair_id": str(repair.id) if repair else None,
        }
    db.commit()
    suffix = _suffix(body.content_type)
    key = s3util.object_key(rooftop_id, body.ro_number, body.content_hash, suffix)
    url = s3util.presign_put(key, body.content_type)
    return {"idempotent": False, "s3_key": key, "upload_url": url, "repair_id": None}


@app.post("/api/uploads/complete")
def complete(body: CompleteIn, db: Session = Depends(get_db)):
    return _complete(
        db,
        rooftop_id=body.rooftop_id,
        ro_number=body.ro_number,
        vin=body.vin,
        content_hash=body.content_hash,
        s3_key=body.s3_key,
        content_type=body.content_type,
        byte_size=body.byte_size,
        parent_repair_id=body.parent_repair_id,
    )


@app.post("/api/uploads/proxy")
async def proxy_upload(
    file: UploadFile = File(...),
    rooftop_id: str = Form(...),
    ro_number: str = Form(...),
    vin: str = Form(""),
    content_hash: str = Form(...),
    parent_repair_id: str = Form(""),
    attach_repair_id: str = Form(""),
    media_kind: str = Form("audio"),
    metadata_json: str = Form("{}"),
    db: Session = Depends(get_db),
):
    data = await file.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != content_hash:
        raise HTTPException(400, "content hash mismatch")
    content_type = file.content_type or "application/octet-stream"
    rooftop = ensure_rooftop(db, rooftop_id)
    key = s3util.object_key(rooftop.id, ro_number, digest, _suffix(content_type))
    s3util.put_bytes(key, data, content_type)
    if attach_repair_id:
        meta = json.loads(metadata_json or "{}")
        return _attach_evidence(
            db,
            repair_id=uuid.UUID(attach_repair_id),
            content_hash=digest,
            s3_key=key,
            content_type=content_type,
            byte_size=len(data),
            media_kind=media_kind,
            metadata=meta,
        )
    return _complete(
        db,
        rooftop_id=rooftop.id,
        ro_number=ro_number,
        vin=vin or None,
        content_hash=digest,
        s3_key=key,
        content_type=content_type,
        byte_size=len(data),
        parent_repair_id=parent_repair_id or None,
    )


@app.post("/api/repairs/{repair_id}/evidence")
def attach_evidence(repair_id: uuid.UUID, body: EvidenceIn, db: Session = Depends(get_db)):
    return _attach_evidence(
        db,
        repair_id=repair_id,
        content_hash=body.content_hash,
        s3_key=body.s3_key,
        content_type=body.content_type,
        byte_size=body.byte_size,
        media_kind=body.media_kind,
        metadata=body.metadata,
    )


@app.post("/api/scenarios")
def run_scenario(body: ScenarioIn, db: Session = Depends(get_db)):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"unknown scenario {body.scenario}")
    rooftop = ensure_rooftop(db, body.rooftop_id)
    ro_number, filename = SCENARIOS[body.scenario]
    path = FIXTURES / filename
    raw_text = path.read_text()
    data = raw_text.encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    key = s3util.object_key(rooftop.id, ro_number, digest, ".txt")
    s3util.put_bytes(key, data, "text/plain; charset=utf-8")
    ro = latest_ro(db, rooftop.id, ro_number)
    return _complete(
        db,
        rooftop_id=rooftop.id,
        ro_number=ro_number,
        vin=ro.vin if ro else None,
        content_hash=digest,
        s3_key=key,
        content_type="text/plain",
        byte_size=len(data),
        parent_repair_id=None,
        scenario=body.scenario,
    )


@app.get("/api/repairs")
def list_repairs(db: Session = Depends(get_db)):
    rows = db.scalars(select(Repair).order_by(Repair.created_at.desc()).limit(50)).all()
    return {"repairs": [_hydrate(db, r) for r in rows]}


@app.get("/api/repairs/{repair_id}")
def get_repair(repair_id: uuid.UUID, db: Session = Depends(get_db)):
    repair = db.get(Repair, repair_id)
    if not repair:
        raise HTTPException(404, "repair not found")
    return _hydrate(db, repair)


@app.get("/api/repairs/{repair_id}/events")
def get_events(repair_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.scalars(select(Event).where(Event.repair_id == repair_id).order_by(Event.id)).all()
    return {"events": [event_out(e) for e in rows]}


@app.post("/api/repairs/{repair_id}/answers")
def submit_answers(repair_id: uuid.UUID, body: AnswersIn, db: Session = Depends(get_db)):
    repair = db.get(Repair, repair_id)
    if not repair:
        raise HTTPException(404, "repair not found")
    story_row = latest_story(db, repair.id)
    if not story_row:
        raise HTTPException(400, "no story to patch")
    non_empty = {k: v for k, v in (body.answers or {}).items() if (v or "").strip()}
    if not non_empty:
        raise HTTPException(400, "Enter at least one measurement before submitting.")
    story = apply_answers(WarrantyStory.model_validate(story_row.payload), non_empty)
    ro = latest_ro(db, repair.rooftop_id, repair.ro_number)
    transcript = db.scalars(
        select(Transcript).where(Transcript.repair_id == repair.id).order_by(Transcript.created_at.desc())
    ).first()
    story = validate(
        story,
        labor_op_name=ro.labor_op_name if ro else None,
        transcript=transcript.text if transcript else "",
    )
    qs = [q.model_dump() for q in questions_for(story.missing_required)]
    row = StoryVersion(
        id=new_id(),
        repair_id=repair.id,
        version=story_row.version + 1,
        schema_version=SCHEMA_VERSION,
        prompt_version=story.prompt_version or PROMPT_VERSION,
        validator_version=validator_meta(),
        payload=json.loads(story.model_dump_json()),
        missing_required=story.missing_required,
        followup_questions=qs,
        source_transcript_id=story_row.source_transcript_id,
    )
    db.add(row)
    emit(
        db,
        repair_id=repair.id,
        stage="validate",
        message="Follow-up answers merged; re-validated",
        data={"missing": story.missing_required, "version": row.version},
    )
    if story.missing_required:
        repair.status = "needs_followup"
    else:
        repair.status = "filing"
        jobs.enqueue(db, job_type="dms_sync", repair_id=repair.id)
        emit(db, repair_id=repair.id, stage="dms", message="Enqueued DMS writeback after follow-up")
    db.commit()
    db.refresh(repair)
    return _hydrate(db, repair)


@app.get("/api/ops/metrics")
def metrics(db: Session = Depends(get_db)):
    total = db.scalar(select(func.count()).select_from(Repair)) or 0
    by_status = dict(db.execute(select(Repair.status, func.count()).group_by(Repair.status)).all())
    filed = by_status.get("filed", 0)
    currently_blocked = by_status.get("needs_followup", 0)
    dlq = by_status.get("dead_letter", 0)

    v1_total = (
        db.scalar(select(func.count()).select_from(StoryVersion).where(StoryVersion.version == 1))
        or 0
    )
    v1_missing = (
        db.scalar(
            select(func.count())
            .select_from(StoryVersion)
            .where(StoryVersion.version == 1, func.cardinality(StoryVersion.missing_required) > 0)
        )
        or 0
    )
    missing_rate = (v1_missing / v1_total) if v1_total else 0.0

    lat = db.scalar(
        select(
            func.avg(
                func.extract("epoch", Repair.ingest_finished_at)
                - func.extract("epoch", Repair.ingest_started_at)
            )
        ).where(Repair.ingest_finished_at.is_not(None), Repair.ingest_started_at.is_not(None))
    )
    jobs_by_status = dict(db.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    return {
        "repairs": total,
        "by_status": by_status,
        "filed": filed,
        "blocked_missing_fields": currently_blocked,
        "caught_missing_fields": v1_missing,
        "validated_repairs": v1_total,
        "missing_field_rate": missing_rate,
        "dead_letter": dlq,
        "avg_latency_s": float(lat) if lat is not None else None,
        "jobs": jobs_by_status,
        "llm_provider": settings.llm_provider,
        "stt_provider": settings.stt_provider,
        "has_groq": bool(settings.groq_api_key),
        "has_openai": bool(settings.openai_api_key),
    }


@app.post("/api/ops/dms-fail-next")
def dms_fail_next(body: DmsFailIn | None = None):
    return dms.fail_next(body.count if body else 1)


@app.post("/api/ops/reset")
def reset_demo(db: Session = Depends(get_db)):
    db.execute(
        text(
            "TRUNCATE events, dms_writebacks, story_versions, transcripts, jobs, repairs, uploads RESTART IDENTITY CASCADE"
        )
    )
    db.commit()
    try:
        dms.reset_dms()
    except Exception:
        pass
    seed(db)
    seed_dummy_media(db)
    return {"ok": True}


@app.get("/api/ops/objects")
def list_objects(db: Session = Depends(get_db)):
    rows = db.scalars(select(Upload).order_by(Upload.created_at.desc()).limit(40)).all()
    return {
        "objects": [
            {
                "id": str(u.id),
                "s3_uri": f"s3://{u.s3_bucket}/{u.s3_key}",
                "s3_key": u.s3_key,
                "content_hash": u.content_hash,
                "content_type": u.content_type,
                "byte_size": u.byte_size,
                "media_kind": u.media_kind,
                "metadata": u.meta or {},
                "repair_id": str(u.repair_id) if u.repair_id else None,
            }
            for u in rows
        ]
    }


@app.get("/api/uploads/{upload_id}/file")
def download_upload(upload_id: uuid.UUID, db: Session = Depends(get_db)):
    upload = db.get(Upload, upload_id)
    if not upload:
        raise HTTPException(404, "upload not found")
    try:
        data = s3util.get_bytes(upload.s3_key)
    except Exception as exc:
        raise HTTPException(404, f"object missing in store: {exc}") from exc
    return Response(content=data, media_type=upload.content_type or "application/octet-stream")


def _attach_evidence(
    db: Session,
    *,
    repair_id: uuid.UUID,
    content_hash: str,
    s3_key: str,
    content_type: str,
    byte_size: int | None,
    media_kind: str,
    metadata: dict,
) -> dict:
    repair = db.get(Repair, repair_id)
    if not repair:
        raise HTTPException(404, "repair not found")
    existing = db.scalars(
        select(Upload).where(
            Upload.rooftop_id == repair.rooftop_id,
            Upload.content_hash == content_hash,
        )
    ).first()
    if existing:
        emit(
            db,
            repair_id=repair.id,
            upload_id=existing.id,
            stage="evidence",
            message="Evidence already linked (idempotent)",
            data={"sha256": content_hash, "media_kind": media_kind},
        )
        db.commit()
        return _hydrate(db, repair, duplicate=True)

    upload = Upload(
        id=new_id(),
        rooftop_id=repair.rooftop_id,
        content_hash=content_hash,
        s3_bucket=settings.s3_bucket,
        s3_key=s3_key,
        content_type=content_type,
        byte_size=byte_size,
        media_kind=media_kind,
        meta=metadata or {},
        repair_id=repair.id,
    )
    db.add(upload)
    db.flush()
    emit(
        db,
        repair_id=repair.id,
        upload_id=upload.id,
        stage="evidence",
        message=f"Attached {media_kind} evidence to repair record",
        data={"sha256": content_hash, "s3_key": s3_key, "byte_size": byte_size},
    )
    db.commit()
    db.refresh(repair)
    return _hydrate(db, repair)


def _complete(
    db: Session,
    *,
    rooftop_id: str,
    ro_number: str,
    vin: str | None,
    content_hash: str,
    s3_key: str,
    content_type: str,
    byte_size: int | None,
    parent_repair_id: str | None,
    scenario: str | None = None,
) -> dict:
    rooftop = ensure_rooftop(db, rooftop_id)
    existing = db.scalars(
        select(Upload).where(Upload.rooftop_id == rooftop.id, Upload.content_hash == content_hash)
    ).first()
    if existing:
        repair = None
        if existing.repair_id:
            repair = db.get(Repair, existing.repair_id)
        if not repair:
            repair = db.scalars(select(Repair).where(Repair.upload_id == existing.id)).first()
        if not repair and parent_repair_id:
            repair = db.get(Repair, uuid.UUID(parent_repair_id))
        if not repair:
            raise HTTPException(409, "upload exists without repair")
        emit(
            db,
            repair_id=repair.id,
            upload_id=existing.id,
            stage="ingest",
            message="Idempotent replay — same content hash, no new job",
            data={"sha256": content_hash},
        )
        db.commit()
        return _hydrate(db, repair, duplicate=True)

    parent = None
    if parent_repair_id:
        parent = db.get(Repair, uuid.UUID(parent_repair_id))
        if not parent:
            raise HTTPException(404, "parent repair not found")

    ro = latest_ro(db, rooftop.id, ro_number)
    resolved_vin = vin or (ro.vin if ro else None) or (parent.vin if parent else None)
    if not resolved_vin:
        raise HTTPException(400, "VIN required")

    upload = Upload(
        id=new_id(),
        rooftop_id=rooftop.id,
        content_hash=content_hash,
        s3_bucket=settings.s3_bucket,
        s3_key=s3_key,
        content_type=content_type,
        byte_size=byte_size,
        media_kind="audio",
        parent_upload_id=parent.upload_id if parent else None,
    )
    db.add(upload)
    db.flush()

    if parent:
        repair = parent
        upload.repair_id = repair.id
        job_type = "followup"
    else:
        repair = Repair(
            id=new_id(),
            rooftop_id=rooftop.id,
            ro_number=ro_number,
            vin=resolved_vin,
            upload_id=upload.id,
            status="queued",
        )
        db.add(repair)
        db.flush()
        upload.repair_id = repair.id
        job_type = "ingest"

    job = jobs.enqueue(db, job_type=job_type, repair_id=repair.id, upload_id=upload.id)
    emit(
        db,
        repair_id=repair.id,
        upload_id=upload.id,
        job_id=job.id,
        stage="ingest",
        message="Upload accepted; job queued",
        data={"job_type": job_type, "sha256": content_hash, "scenario": scenario, "s3_key": s3_key},
    )
    db.commit()
    db.refresh(repair)
    return _hydrate(db, repair)


def _suffix(content_type: str) -> str:
    ct = (content_type or "").lower()
    if "webm" in ct:
        return ".webm"
    if "quicktime" in ct or "mov" in ct:
        return ".mov"
    if "video/mp4" in ct:
        return ".mp4"
    if "mp4" in ct or "m4a" in ct:
        return ".m4a"
    if "mpeg" in ct or "mp3" in ct:
        return ".mp3"
    if "wav" in ct:
        return ".wav"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "text" in ct:
        return ".txt"
    return ".bin"
