from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "warranty_story.v1"
PROMPT_VERSION = "extract.v1"
VALIDATOR_VERSION = "warranty_rules.v1"

MEASUREMENT_NAMES = [
    "front_pad_thickness_left_mm",
    "front_pad_thickness_right_mm",
    "front_rotor_thickness_left_mm",
    "front_rotor_thickness_right_mm",
    "front_rotor_min_spec_mm",
    "caliper_bracket_torque_nm",
    "lug_nut_torque_nm",
    "test_drive_miles",
    "battery_midtronics_cca",
    "battery_spec_cca",
    "battery_voltage_v",
]


class Measurement(BaseModel):
    name: str
    value: float | None = None
    unit: str = ""
    spec_min: float | None = None
    source: Literal["spoken", "prompted", "dms"] = "spoken"


class Part(BaseModel):
    name: str
    part_number: str | None = None
    quantity: int = 1
    action: Literal["replaced", "inspected", "reused", "recommended"] = "replaced"


class WarrantyStory(BaseModel):
    schema_version: str = SCHEMA_VERSION
    prompt_version: str = PROMPT_VERSION
    repair_type: Literal["brake", "misfire", "battery", "other"] = "other"
    concern: str = ""
    cause: str = ""
    correction: str = ""
    dtcs: list[str] = Field(default_factory=list)
    cylinder: int | None = None
    parts: list[Part] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)
    labor_op: str | None = None
    codes_cleared: bool | None = None
    outcome: Literal["verified_fixed", "needs_followup", "not_reproduced"] = "needs_followup"
    missing_required: list[str] = Field(default_factory=list)


class FollowupQuestion(BaseModel):
    field: str
    question: str
    unit: str | None = None
    input_type: Literal["number", "text"] = "number"
