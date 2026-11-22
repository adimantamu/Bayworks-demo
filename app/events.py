from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Event


def emit(
    db: Session,
    *,
    stage: str,
    message: str,
    repair_id: uuid.UUID | None = None,
    upload_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    level: str = "info",
    data: dict | None = None,
) -> None:
    db.add(
        Event(
            repair_id=repair_id,
            upload_id=upload_id,
            job_id=job_id,
            stage=stage,
            message=message,
            level=level,
            data=data or {},
        )
    )
    db.flush()
