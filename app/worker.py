from __future__ import annotations

import time
import traceback

from app import s3util
from app.config import settings
from app.db import SessionLocal, init_db
from app.events import emit
from app.jobs import claim_job, fail, succeed
from app.models import Job, Repair
from app.pipeline import process_job, set_status
from app.seed import seed
from app.transcribe import PermanentJobError


def main() -> None:
    init_db()
    s3util.ensure_bucket()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    print(f"[worker] {settings.worker_id} polling jobs", flush=True)
    while True:
        _tick()


def _tick() -> None:
    db = SessionLocal()
    try:
        job = claim_job(db, settings.worker_id)
        if not job:
            db.commit()
            time.sleep(0.35)
            return
        job_id = job.id
        db.commit()
    except Exception:
        db.rollback()
        time.sleep(1)
        return
    finally:
        db.close()

    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        process_job(db, job)
        succeed(db, job)
        db.commit()
        print(f"[worker] {job.job_type} {job.id} ok", flush=True)
    except Exception as exc:
        db.rollback()
        job = db.get(Job, job_id)
        permanent = isinstance(exc, PermanentJobError)
        err = f"{exc}\n{traceback.format_exc()}"
        fail(db, job, err, permanent=permanent)
        if job.repair_id:
            emit(
                db,
                repair_id=job.repair_id,
                job_id=job.id,
                stage=job.job_type,
                level="error",
                message=f"{exc} (attempt {job.attempts}/{job.max_attempts})" if not permanent else str(exc),
                data={"attempt": job.attempts, "max_attempts": job.max_attempts, "dead_letter": job.status == "dead_letter"},
            )
            if job.status == "dead_letter":
                repair = db.get(Repair, job.repair_id)
                if repair:
                    set_status(db, repair, "dead_letter")
        db.commit()
        print(f"[worker] {job.job_type} {job.id} fail: {exc}", flush=True)
        time.sleep(0.3)
    finally:
        db.close()


if __name__ == "__main__":
    main()
