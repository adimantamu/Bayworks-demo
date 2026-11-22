from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Job, new_id


def enqueue(
    db: Session,
    *,
    job_type: str,
    repair_id: uuid.UUID | None = None,
    upload_id: uuid.UUID | None = None,
    delay_seconds: int = 0,
) -> Job:
    job = Job(
        id=new_id(),
        job_type=job_type,
        status="queued",
        repair_id=repair_id,
        upload_id=upload_id,
        run_after=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds),
    )
    db.add(job)
    db.flush()
    return job


def claim_job(db: Session, worker_id: str) -> Job | None:
    row = db.execute(
        text(
            """
            UPDATE jobs
            SET status = 'running',
                locked_at = now(),
                locked_by = :worker,
                attempts = attempts + 1,
                updated_at = now()
            WHERE id = (
                SELECT id FROM jobs
                WHERE status = 'queued'
                  AND run_after <= now()
                  AND attempts < max_attempts
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id
            """
        ),
        {"worker": worker_id},
    ).first()
    if not row:
        return None
    return db.get(Job, row[0])


def succeed(db: Session, job: Job) -> None:
    job.status = "succeeded"
    job.updated_at = datetime.now(timezone.utc)
    job.last_error = None


def fail(db: Session, job: Job, error: str, *, permanent: bool = False) -> None:
    job.last_error = error[:4000]
    job.updated_at = datetime.now(timezone.utc)
    if permanent or job.attempts >= job.max_attempts:
        job.status = "dead_letter"
    else:
        job.status = "queued"
        delay = min(settings.retry_max_seconds, settings.retry_base_seconds * (2 ** (job.attempts - 1)))
        job.run_after = datetime.now(timezone.utc) + timedelta(seconds=delay)
