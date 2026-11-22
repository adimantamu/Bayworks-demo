from __future__ import annotations

import httpx

from app.config import settings


class DmsError(Exception):
    def __init__(self, status: int, body: dict):
        super().__init__(f"DMS {status}: {body.get('detail', body)}")
        self.status = status
        self.body = body


def fetch_repair_order(ro_number: str) -> dict:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(f"{settings.mock_dms_url}/repair-orders/{ro_number}")
        r.raise_for_status()
        return r.json()


def push_story(ro_number: str, payload: dict, idempotency_key: str) -> tuple[int, dict]:
    with httpx.Client(timeout=10.0) as client:
        r = client.put(
            f"{settings.mock_dms_url}/repair-orders/{ro_number}/story",
            json=payload,
            headers={"Idempotency-Key": idempotency_key},
        )
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text}
        if r.status_code >= 400:
            raise DmsError(r.status_code, body)
        return r.status_code, body


def fail_next(count: int = 1) -> dict:
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{settings.mock_dms_url}/admin/fail-next", json={"count": count})
        r.raise_for_status()
        return r.json()


def reset_dms() -> dict:
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{settings.mock_dms_url}/admin/reset")
        r.raise_for_status()
        return r.json()
