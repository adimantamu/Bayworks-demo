from app.extract import _heuristic_extract, apply_answers
from app.validate import validate

BRAKE_OP = "BRAKE: FRONT BRAKE SERVICE"
MISFIRE_OP = "ENGINE: CYLINDER 1 MISFIRE DIAGNOSIS"
BATTERY_OP = "ELECTRICAL: 12V BATTERY TEST AND REPLACE"


def _load(name: str) -> str:
    return open(f"fixtures/transcripts/{name}.txt").read()


def test_complete_brake_files():
    text = _load("brake_complete")
    story = _heuristic_extract(text, BRAKE_OP, None)
    story = validate(story, labor_op_name=BRAKE_OP, transcript=text)
    assert story.repair_type == "brake"
    assert story.missing_required == []
    assert story.outcome == "verified_fixed"


def test_incomplete_brake_blocks():
    text = _load("brake_incomplete")
    story = _heuristic_extract(text, BRAKE_OP, None)
    story = validate(story, labor_op_name=BRAKE_OP, transcript=text)
    assert "front_rotor_thickness_mm" in story.missing_required
    assert "caliper_bracket_torque_nm" in story.missing_required
    assert story.outcome == "needs_followup"


def test_followup_answers_clear_gate():
    text = _load("brake_incomplete")
    story = validate(
        _heuristic_extract(text, BRAKE_OP, None),
        labor_op_name=BRAKE_OP,
        transcript=text,
    )
    story = apply_answers(
        story,
        {
            "front_pad_thickness_left_mm": "2 / 2",
            "front_rotor_thickness_left_mm": "22.5 spec 25",
            "caliper_bracket_torque_nm": "110",
            "test_drive_miles": "5",
        },
    )
    story = validate(story, labor_op_name=BRAKE_OP, transcript=text)
    assert story.missing_required == []


def test_apply_answers_rewrites_3cs_from_prompted_measurements():
    text = _load("brake_incomplete")
    story = validate(
        _heuristic_extract(text, BRAKE_OP, None),
        labor_op_name=BRAKE_OP,
        transcript=text,
    )
    story = apply_answers(
        story,
        {
            "front_pad_thickness_left_mm": "2 / 2",
            "front_rotor_thickness_left_mm": "22.5 spec 25",
            "caliper_bracket_torque_nm": "110",
            "test_drive_miles": "5",
        },
    )
    assert text.strip() not in story.cause
    assert "2mm" in story.cause
    assert "22.5mm" in story.cause
    assert "25mm" in story.cause
    assert "110 Nm" in story.correction
    assert "5-mile" in story.correction
    prompted = {m.name: m.source for m in story.measurements}
    assert prompted["front_pad_thickness_left_mm"] == "prompted"
    assert prompted["front_rotor_min_spec_mm"] == "prompted"


def test_complete_misfire_files():
    text = _load("misfire_complete")
    story = _heuristic_extract(text, MISFIRE_OP, None)
    story = validate(story, labor_op_name=MISFIRE_OP, transcript=text)
    assert story.repair_type == "misfire"
    assert "P0301" in story.dtcs
    assert story.cylinder == 1
    assert story.codes_cleared is True
    assert story.missing_required == []
    assert story.outcome == "verified_fixed"
    assert "P0301" in story.cause
    assert "cylinder 1" in story.correction
    assert "30520-5A2-A01" in story.correction


def test_incomplete_misfire_blocks():
    text = _load("misfire_incomplete")
    story = _heuristic_extract(text, MISFIRE_OP, None)
    story = validate(story, labor_op_name=MISFIRE_OP, transcript=text)
    assert story.repair_type == "misfire"
    assert "dtc" in story.missing_required
    assert "cylinder" in story.missing_required
    assert "codes_cleared" in story.missing_required
    assert "test_drive_miles" in story.missing_required
    assert story.outcome == "needs_followup"


def test_complete_battery_files():
    text = _load("battery_complete")
    story = _heuristic_extract(text, BATTERY_OP, None)
    story = validate(story, labor_op_name=BATTERY_OP, transcript=text)
    assert story.repair_type == "battery"
    assert story.missing_required == []
    assert story.outcome == "verified_fixed"
    names = {m.name: m.value for m in story.measurements}
    assert names["battery_midtronics_cca"] == 380
    assert names["battery_spec_cca"] == 550
    assert "380 CCA" in story.cause
    assert "550 CCA" in story.cause


def test_codes_cleared_natural_language():
    text = _load("misfire_incomplete")
    story = validate(
        _heuristic_extract(text, MISFIRE_OP, None),
        labor_op_name=MISFIRE_OP,
        transcript=text,
    )
    story = apply_answers(
        story,
        {
            "dtc": "P0301",
            "cylinder": "1",
            "codes_cleared": "yep, cleared them",
            "test_drive_miles": "3",
        },
    )
    assert story.codes_cleared is True
    story = apply_answers(story, {"codes_cleared": "no, didn't clear"})
    assert story.codes_cleared is False
    story = validate(story, labor_op_name=MISFIRE_OP, transcript=text)
    assert "codes_cleared" in story.missing_required
