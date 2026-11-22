from __future__ import annotations

import hashlib

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import s3util
from app.config import settings
from app.models import RepairOrder, Rooftop, Upload, new_id

ROOFTOP = {
    "id": "keegan-honda",
    "name": "Keegan Honda Service",
    "dealer_group": "Keegan Automotive ($500M+)",
}

ORDERS = [
    {
        "ro_number": "24-1009",
        "vin": "2HKRS5H59NH123456",
        "mileage": 41208,
        "labor_op": "BRK-FSA",
        "labor_op_name": "BRAKE: FRONT BRAKE SERVICE",
        "vehicle_year": 2022,
        "vehicle_make": "Honda",
        "vehicle_model": "CR-V",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Harsh squeal from the front axle when braking under 30 mph",
    },
    {
        "ro_number": "24-1088",
        "vin": "1HGCV1F34NA654321",
        "mileage": 28940,
        "labor_op": "ENG-MF1",
        "labor_op_name": "ENGINE: CYLINDER 1 MISFIRE DIAGNOSIS",
        "vehicle_year": 2022,
        "vehicle_make": "Honda",
        "vehicle_model": "Accord",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Check engine light on, runs rough at idle",
    },
    {
        "ro_number": "24-1102",
        "vin": "5J6RW2H89NL998877",
        "mileage": 67112,
        "labor_op": "ELE-BAT",
        "labor_op_name": "ELECTRICAL: 12V BATTERY TEST AND REPLACE",
        "vehicle_year": 2021,
        "vehicle_make": "Honda",
        "vehicle_model": "CR-V",
        "advisor": "B. Keegan",
        "technician": "Javi",
        "customer_concern": "Slow crank, hard start in the morning",
    },
]


def ensure_rooftop(db: Session, rooftop_id: str) -> Rooftop:
    rooftop_id = (rooftop_id or ROOFTOP["id"]).strip() or ROOFTOP["id"]
    existing = db.get(Rooftop, rooftop_id)
    if existing:
        return existing
    name = ROOFTOP["name"] if rooftop_id == ROOFTOP["id"] else f"{rooftop_id} Service"
    group = ROOFTOP["dealer_group"]
    row = Rooftop(id=rooftop_id, name=name, dealer_group=group)
    db.add(row)
    db.flush()
    for order in ORDERS:
        db.add(RepairOrder(id=new_id(), rooftop_id=rooftop_id, **order))
    db.flush()
    return row


def seed(db: Session) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(842002)"))
    ensure_rooftop(db, ROOFTOP["id"])
    for row in ORDERS:
        exists = db.scalars(
            select(RepairOrder).where(
                RepairOrder.rooftop_id == ROOFTOP["id"],
                RepairOrder.ro_number == row["ro_number"],
            )
        ).first()
        if not exists:
            db.add(RepairOrder(id=new_id(), rooftop_id=ROOFTOP["id"], **row))
    db.commit()


def _tiny_mp4(label: bytes) -> bytes:
    ftyp = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    payload = label + b"\x00" * 4096
    mdat = (8 + len(payload)).to_bytes(4, "big") + b"mdat" + payload
    return ftyp + mdat


def _tiny_mps(label: bytes) -> bytes:
    pack = bytes.fromhex("000001ba210001000180000001bb000c80c4802001e17e")
    return pack + b"\x00" + label + b"\x00" * 2048


def seed_dummy_media(db: Session) -> None:
    ensure_rooftop(db, ROOFTOP["id"])
    files = [
        ("24-1009", "video", "video/mp4", ".mp4", _tiny_mp4(b"bay-brake-pad-closeup")),
        ("24-1009", "video", "video/mpeg", ".mps", _tiny_mps(b"bay-brake-torque-cam")),
        ("24-1088", "video", "video/mp4", ".mp4", _tiny_mp4(b"bay-misfire-scope")),
    ]
    for ro_number, kind, content_type, suffix, body in files:
        digest = hashlib.sha256(body).hexdigest()
        existing = db.scalars(
            select(Upload).where(Upload.rooftop_id == ROOFTOP["id"], Upload.content_hash == digest)
        ).first()
        if existing:
            continue
        key = s3util.object_key(ROOFTOP["id"], ro_number, digest, suffix)
        s3util.put_bytes(key, body, content_type)
        db.add(
            Upload(
                id=new_id(),
                rooftop_id=ROOFTOP["id"],
                content_hash=digest,
                s3_bucket=settings.s3_bucket,
                s3_key=key,
                content_type=content_type,
                byte_size=len(body),
                media_kind=kind,
                meta={
                    "filename": f"dummy-{ro_number}{suffix}",
                    "seeded": True,
                    "note": "synthetic bay-camera object for S3 + catalog demo",
                },
            )
        )
    db.commit()
