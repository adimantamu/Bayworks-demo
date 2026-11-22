from __future__ import annotations

import os
import threading
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock DMS", version="0.1.0")
_lock = threading.Lock()
_fail_next = 0
_fail_rate = float(os.environ.get("MOCK_DMS_FAIL_RATE", "0"))

ORDERS = {
    "24-1009": {
        "ro_number": "24-1009",
        "vin": "2HKRS5H59NH123456",
        "mileage": 41208,
        "labor_op": "BRK-FSA",
        "labor_op_name": "BRAKE: FRONT BRAKE SERVICE",
        "hours": 1.8,
        "vehicle_year": 2022,
        "vehicle_make": "Honda",
        "vehicle_model": "CR-V",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Harsh squeal from the front axle when braking under 30 mph",
        "line": 1,
    },
    "24-1088": {
        "ro_number": "24-1088",
        "vin": "1HGCV1F34NA654321",
        "mileage": 28940,
        "labor_op": "ENG-MF1",
        "labor_op_name": "ENGINE: CYLINDER 1 MISFIRE DIAGNOSIS",
        "hours": 1.2,
        "vehicle_year": 2022,
        "vehicle_make": "Honda",
        "vehicle_model": "Accord",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Check engine light on, runs rough at idle",
        "line": 1,
    },
    "24-1102": {
        "ro_number": "24-1102",
        "vin": "5J6RW2H89NL998877",
        "mileage": 67112,
        "labor_op": "ELE-BAT",
        "labor_op_name": "ELECTRICAL: 12V BATTERY TEST AND REPLACE",
        "hours": 0.5,
        "vehicle_year": 2021,
        "vehicle_make": "Honda",
        "vehicle_model": "CR-V",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Slow crank, hard start in the morning",
        "line": 1,
    },
}

STORIES: dict[str, dict] = {}
IDEMPOTENCY: dict[str, dict] = {}


class FailNextIn(BaseModel):
    count: int = 1


class StoryIn(BaseModel):
    ro_number: str
    vin: str
    rooftop_id: str
    story: dict
    audio_sha256: str | None = None
    s3_uri: str | None = None
    evidence_uris: list[str] | None = None
    schema_version: str | None = None
    prompt_version: str | None = None
    validator_version: str | None = None
    story_version: int | None = None


@app.get("/health")
def health():
    return {"ok": True, "system": "mock-cdk"}


@app.get("/repair-orders/{ro_number}")
def get_ro(ro_number: str):
    row = ORDERS.get(ro_number)
    if not row:
        raise HTTPException(404, "repair order not found")
    return row


@app.post("/admin/fail-next")
def fail_next(body: FailNextIn | None = None):
    global _fail_next
    add = max(1, body.count) if body else 1
    with _lock:
        _fail_next += add
        n = _fail_next
    return {"fail_next": n}


@app.post("/admin/reset")
def reset():
    global _fail_next
    with _lock:
        _fail_next = 0
        STORIES.clear()
        IDEMPOTENCY.clear()
    return {"ok": True}


@app.get("/admin/stories")
def list_stories():
    return {"stories": list(STORIES.values()), "fail_next": _fail_next}


@app.put("/repair-orders/{ro_number}/story")
def put_story(
    ro_number: str,
    body: StoryIn,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if ro_number not in ORDERS:
        raise HTTPException(404, "repair order not found")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key required")

    global _fail_next
    with _lock:
        if idempotency_key in IDEMPOTENCY:
            return IDEMPOTENCY[idempotency_key]
        if _fail_next > 0:
            _fail_next -= 1
            raise HTTPException(503, f"DMS unavailable (injected fault, {_fail_next} remaining)")

    story_id = str(uuid.uuid4())
    record = {
        "story_id": story_id,
        "ro_number": ro_number,
        "accepted_at": datetime.now(timezone.utc).isoformat(),
        "status": "accepted",
        "payload": body.model_dump(),
    }
    STORIES[story_id] = record
    IDEMPOTENCY[idempotency_key] = record
    return record
