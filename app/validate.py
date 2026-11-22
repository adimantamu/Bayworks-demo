from app.warranty import FollowupQuestion, VALIDATOR_VERSION, WarrantyStory

QUESTIONS: dict[str, FollowupQuestion] = {
    "front_pad_thickness_mm": FollowupQuestion(
        field="front_pad_thickness_left_mm",
        question="What was the front brake pad thickness on the left and right, in mm?",
        unit="mm",
    ),
    "front_rotor_thickness_mm": FollowupQuestion(
        field="front_rotor_thickness_left_mm",
        question="What was the measured front rotor thickness (L/R) in mm, and what is the minimum spec?",
        unit="mm",
    ),
    "caliper_bracket_torque_nm": FollowupQuestion(
        field="caliper_bracket_torque_nm",
        question="What torque did you apply to the caliper bracket bolts, in Nm?",
        unit="Nm",
    ),
    "test_drive_miles": FollowupQuestion(
        field="test_drive_miles",
        question="How many miles was the verification road test, and was the concern gone?",
        unit="miles",
    ),
    "dtc": FollowupQuestion(
        field="dtc",
        question="Which DTC did you pull (for example P0301)?",
        unit=None,
        input_type="text",
    ),
    "cylinder": FollowupQuestion(
        field="cylinder",
        question="Which cylinder was the misfire / replaced coil on?",
        unit=None,
        input_type="number",
    ),
    "codes_cleared": FollowupQuestion(
        field="codes_cleared",
        question="Did you clear the codes after the repair? Yes or no.",
        unit=None,
        input_type="text",
    ),
    "battery_midtronics_cca": FollowupQuestion(
        field="battery_midtronics_cca",
        question="What was the Midtronics / conductance reading in CCA, and what is spec?",
        unit="CCA",
    ),
    "concern": FollowupQuestion(field="concern", question="What was the customer concern?", input_type="text"),
    "cause": FollowupQuestion(field="cause", question="What did diagnosis find (tests, measurements, failed part)?", input_type="text"),
    "correction": FollowupQuestion(field="correction", question="What correction was performed, including verification?", input_type="text"),
}


def _has_measurement(story: WarrantyStory, names: list[str]) -> bool:
    present = {m.name for m in story.measurements if m.value is not None}
    return any(name in present for name in names)


def _detect_type(story: WarrantyStory, labor_op_name: str | None, transcript: str) -> str:
    if story.repair_type and story.repair_type != "other":
        return story.repair_type
    blob = f"{labor_op_name or ''} {transcript} {story.concern} {story.cause} {story.correction}".lower()
    if "brake" in blob or "rotor" in blob or "pad" in blob:
        return "brake"
    if "p030" in blob or "misfire" in blob or "coil" in blob:
        return "misfire"
    if "battery" in blob or "midtronic" in blob or "crank" in blob:
        return "battery"
    return "other"


def validate(story: WarrantyStory, *, labor_op_name: str | None = None, transcript: str = "") -> WarrantyStory:
    story.repair_type = _detect_type(story, labor_op_name, transcript)  # type: ignore[assignment]
    missing: list[str] = []

    if not (story.concern or "").strip():
        missing.append("concern")
    if not (story.cause or "").strip():
        missing.append("cause")
    if not (story.correction or "").strip():
        missing.append("correction")

    if story.repair_type == "brake":
        if not _has_measurement(
            story, ["front_pad_thickness_left_mm", "front_pad_thickness_right_mm"]
        ):
            missing.append("front_pad_thickness_mm")
        if not _has_measurement(
            story, ["front_rotor_thickness_left_mm", "front_rotor_thickness_right_mm"]
        ):
            missing.append("front_rotor_thickness_mm")
        if not _has_measurement(story, ["caliper_bracket_torque_nm"]):
            missing.append("caliper_bracket_torque_nm")
        if not _has_measurement(story, ["test_drive_miles"]):
            missing.append("test_drive_miles")

    elif story.repair_type == "misfire":
        if not story.dtcs:
            missing.append("dtc")
        if story.cylinder is None:
            missing.append("cylinder")
        if story.codes_cleared is not True:
            missing.append("codes_cleared")
        if not _has_measurement(story, ["test_drive_miles"]):
            missing.append("test_drive_miles")

    elif story.repair_type == "battery":
        if not _has_measurement(story, ["battery_midtronics_cca"]):
            missing.append("battery_midtronics_cca")

    story.missing_required = missing
    if missing:
        story.outcome = "needs_followup"
    elif story.outcome == "needs_followup":
        story.outcome = "verified_fixed"
    return story


def questions_for(missing: list[str]) -> list[FollowupQuestion]:
    out: list[FollowupQuestion] = []
    seen: set[str] = set()
    for field in missing:
        q = QUESTIONS.get(field)
        if q and q.field not in seen:
            out.append(q)
            seen.add(q.field)
    return out[:4]


def validator_meta() -> str:
    return VALIDATOR_VERSION
