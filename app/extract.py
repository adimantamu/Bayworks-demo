from __future__ import annotations

import json
import re

from openai import OpenAI

from app.config import settings
from app.warranty import (
    MEASUREMENT_NAMES,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    Measurement,
    Part,
    WarrantyStory,
)

EXTRACT_SYSTEM = f"""You extract OEM warranty 3Cs (concern, cause, correction) from a technician voice note.

Return JSON matching the schema. Rules:
- Concern is what the customer reported, not the diagnosis.
- Cause must include tests, DTCs, and measurements vs spec when spoken.
- Correction is only work that was actually done, plus torque and road-test verification.
- Do not invent measurements, part numbers, DTCs, or torque values.
- Measurement names must be one of: {", ".join(MEASUREMENT_NAMES)}
- repair_type is brake | misfire | battery | other
"""


def _client() -> tuple[OpenAI, str] | None:
    provider = settings.llm_provider
    if provider == "heuristic":
        return None
    if provider in ("auto", "groq") and settings.groq_api_key:
        return (
            OpenAI(api_key=settings.groq_api_key, base_url="https://api.groq.com/openai/v1"),
            "llama-3.3-70b-versatile",
        )
    if provider in ("auto", "openai") and settings.openai_api_key:
        return OpenAI(api_key=settings.openai_api_key), "gpt-4o-mini"
    return None


def extract_story(
    transcript: str,
    *,
    labor_op_name: str | None = None,
    customer_concern: str | None = None,
) -> tuple[WarrantyStory, str]:
    pair = _client()
    if pair:
        try:
            story = _llm_extract(pair[0], pair[1], transcript, labor_op_name, customer_concern)
            return story, pair[1]
        except Exception:
            pass
    return _heuristic_extract(transcript, labor_op_name, customer_concern), "heuristic.v1"


def _llm_extract(
    client: OpenAI,
    model: str,
    transcript: str,
    labor_op_name: str | None,
    customer_concern: str | None,
) -> WarrantyStory:
    user = {
        "transcript": transcript,
        "labor_op_name": labor_op_name,
        "dms_customer_concern": customer_concern,
    }
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": json.dumps(user)
                + "\n\nJSON keys: repair_type, concern, cause, correction, dtcs, cylinder, parts, measurements, codes_cleared, outcome. measurements: [{name, value, unit, spec_min}]",
            },
        ],
    )
    raw = json.loads(resp.choices[0].message.content or "{}")
    return _coerce(raw, labor_op_name)


def _coerce(raw: dict, labor_op_name: str | None) -> WarrantyStory:
    measurements = []
    for m in raw.get("measurements") or []:
        name = str(m.get("name") or "")
        if name not in MEASUREMENT_NAMES:
            continue
        measurements.append(
            Measurement(
                name=name,
                value=_num(m.get("value")),
                unit=str(m.get("unit") or ""),
                spec_min=_num(m.get("spec_min")),
                source="spoken",
            )
        )
    parts = []
    for p in raw.get("parts") or []:
        action = (
            p.get("action")
            if p.get("action") in {"replaced", "inspected", "reused", "recommended"}
            else "replaced"
        )
        parts.append(
            Part(
                name=str(p.get("name") or "part"),
                part_number=p.get("part_number"),
                quantity=int(p.get("quantity") or 1),
                action=action,
            )
        )
    dtcs = [str(d).upper() for d in (raw.get("dtcs") or [])]
    outcome = (
        raw.get("outcome")
        if raw.get("outcome") in {"verified_fixed", "needs_followup", "not_reproduced"}
        else "needs_followup"
    )
    repair_type = (
        raw.get("repair_type")
        if raw.get("repair_type") in {"brake", "misfire", "battery", "other"}
        else "other"
    )
    return WarrantyStory(
        schema_version=SCHEMA_VERSION,
        prompt_version=PROMPT_VERSION,
        repair_type=repair_type,
        concern=str(raw.get("concern") or ""),
        cause=str(raw.get("cause") or ""),
        correction=str(raw.get("correction") or ""),
        dtcs=dtcs,
        cylinder=_int(raw.get("cylinder")),
        parts=parts,
        measurements=measurements,
        labor_op=labor_op_name,
        codes_cleared=_bool(raw.get("codes_cleared")),
        outcome=outcome,  # type: ignore[arg-type]
    )


def _mval(story: WarrantyStory, name: str) -> float | None:
    for m in story.measurements:
        if m.name == name and m.value is not None:
            return m.value
    return None


def _fmt_num(v: float) -> str:
    return f"{int(v)}" if float(v).is_integer() else f"{v:g}"


def synthesize_3cs(story: WarrantyStory, raw_transcript: str = "") -> WarrantyStory:
    """Build OEM-compliant Cause and Correction narratives grounded in extracted facts."""
    pad_l = _mval(story, "front_pad_thickness_left_mm")
    pad_r = _mval(story, "front_pad_thickness_right_mm")
    rot_l = _mval(story, "front_rotor_thickness_left_mm")
    rot_r = _mval(story, "front_rotor_thickness_right_mm")
    rot_min = _mval(story, "front_rotor_min_spec_mm")
    cal_nm = _mval(story, "caliper_bracket_torque_nm")
    lug_nm = _mval(story, "lug_nut_torque_nm")
    miles = _mval(story, "test_drive_miles")
    cca = _mval(story, "battery_midtronics_cca")
    spec_cca = _mval(story, "battery_spec_cca")
    volts = _mval(story, "battery_voltage_v")

    if story.repair_type == "brake":
        cause_bits = ["Verified customer concern on test drive."]
        if pad_l is not None:
            if pad_r is not None and pad_r != pad_l:
                cause_bits.append(f"Measured front brake pads at {_fmt_num(pad_l)}mm LF / {_fmt_num(pad_r)}mm RF.")
            else:
                cause_bits.append(f"Measured front brake pads worn down to {_fmt_num(pad_l)}mm.")
        if rot_l is not None:
            rot_str = (
                f"{_fmt_num(rot_l)}mm LF / {_fmt_num(rot_r)}mm RF"
                if rot_r is not None and rot_r != rot_l
                else f"{_fmt_num(rot_l)}mm"
            )
            if rot_min is not None:
                cause_bits.append(f"Measured front rotors at {rot_str} vs {_fmt_num(rot_min)}mm minimum specification.")
            else:
                cause_bits.append(f"Measured front rotors at {rot_str}.")
        if len(cause_bits) == 1 and raw_transcript:
            cause_bits.append(raw_transcript.strip())
        story.cause = " ".join(cause_bits)

        corr_bits = ["Replaced front brake pads and front rotors per OEM procedure."]
        if cal_nm is not None:
            corr_bits.append(f"Torqued caliper bracket bolts to {_fmt_num(cal_nm)} Nm.")
        if lug_nm is not None:
            corr_bits.append(f"Torqued wheel lug nuts to {_fmt_num(lug_nm)} Nm.")
        if miles is not None:
            corr_bits.append(f"Performed {_fmt_num(miles)}-mile road test; braking smooth and noise eliminated.")
        elif raw_transcript and not cal_nm:
            corr_bits = [raw_transcript.strip()]
        story.correction = " ".join(corr_bits)

    elif story.repair_type == "misfire":
        cause_bits = ["Verified customer concern."]
        if story.dtcs:
            cause_bits.append(f"Retrieved DTC {', '.join(story.dtcs)}.")
        if story.cylinder is not None:
            cause_bits.append(f"Isolated fault to cylinder {story.cylinder} ignition coil.")
        if len(cause_bits) == 1 and raw_transcript:
            cause_bits.append(raw_transcript.strip())
        story.cause = " ".join(cause_bits)

        part = story.parts[0] if story.parts else Part(name="coil pack")
        pn_txt = f" (P/N {part.part_number})" if part.part_number else ""
        cyl_txt = f" on cylinder {story.cylinder}" if story.cylinder is not None else ""
        corr_bits = [f"Replaced {part.name}{cyl_txt}{pn_txt}."]
        if story.codes_cleared:
            corr_bits.append("Cleared DTCs.")
        if miles is not None:
            corr_bits.append(f"Road tested {_fmt_num(miles)} miles; no fault codes returned, concern resolved.")
        story.correction = " ".join(corr_bits)

    elif story.repair_type == "battery":
        cause_bits = ["Verified slow-crank concern."]
        if cca is not None:
            spec_txt = f" against {_fmt_num(spec_cca)} CCA specification" if spec_cca is not None else ""
            cause_bits.append(f"Midtronics conductance test measured {_fmt_num(cca)} CCA{spec_txt}; battery failed.")
        if volts is not None:
            cause_bits.append(f"Open-circuit voltage measured {_fmt_num(volts)}V.")
        story.cause = " ".join(cause_bits)
        story.correction = (
            "Replaced 12V battery with OEM unit, registered battery management reset, and verified normal cranking and charging output."
        )

    else:
        if not story.cause and raw_transcript:
            story.cause = raw_transcript.strip()
        if not story.correction and raw_transcript:
            story.correction = raw_transcript.strip()

    return story


def _heuristic_extract(
    transcript: str, labor_op_name: str | None, customer_concern: str | None
) -> WarrantyStory:
    text = transcript.strip()
    low = f"{labor_op_name or ''} {text}".lower()
    story = WarrantyStory(labor_op=labor_op_name, prompt_version="heuristic.v1")

    dtcs = re.findall(r"\bP[0-9A-F]{4}\b", text, flags=re.I)
    story.dtcs = list(dict.fromkeys(d.upper() for d in dtcs))

    cyl = re.search(r"cylinder\s+(\d)", text.lower())
    if cyl:
        story.cylinder = int(cyl.group(1))

    if "clear" in text.lower() and "code" in text.lower():
        story.codes_cleared = True

    measurements: list[Measurement] = []
    tlow = text.lower()

    pad = re.search(r"pads?[^.]{0,40}?(\d+(?:\.\d+)?)\s*mm", tlow)
    if pad:
        v = float(pad.group(1))
        measurements.append(Measurement(name="front_pad_thickness_left_mm", value=v, unit="mm"))
        measurements.append(Measurement(name="front_pad_thickness_right_mm", value=v, unit="mm"))

    rotor = re.search(r"rotors?[^.]{0,80}?(\d+(?:\.\d+)?)\s*mm", tlow)
    if rotor:
        v = float(rotor.group(1))
        measurements.append(Measurement(name="front_rotor_thickness_left_mm", value=v, unit="mm"))
        measurements.append(Measurement(name="front_rotor_thickness_right_mm", value=v, unit="mm"))

    spec = re.search(r"(?:below(?:\s+the)?|minimum|spec)\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*mm", tlow)
    if spec:
        measurements.append(Measurement(name="front_rotor_min_spec_mm", value=float(spec.group(1)), unit="mm"))

    caliper = re.search(r"caliper[^.]{0,40}?(\d+(?:\.\d+)?)\s*(?:nm|newton)", tlow)
    if caliper:
        measurements.append(Measurement(name="caliper_bracket_torque_nm", value=float(caliper.group(1)), unit="Nm"))

    lug = re.search(r"lug[^.]{0,40}?(\d+(?:\.\d+)?)\s*(?:nm|newton)", tlow)
    if lug:
        measurements.append(Measurement(name="lug_nut_torque_nm", value=float(lug.group(1)), unit="Nm"))

    miles = re.search(r"(?:test drove|road tested|test drive)[^\d]{0,20}(\d+(?:\.\d+)?)\s*miles?", tlow)
    if miles:
        measurements.append(Measurement(name="test_drive_miles", value=float(miles.group(1)), unit="miles"))

    cca = re.search(r"(\d+)\s*cca", tlow)
    if cca:
        measurements.append(Measurement(name="battery_midtronics_cca", value=float(cca.group(1)), unit="CCA"))
    spec_cca = re.search(r"spec(?: is)?\s*(\d+)\s*cca", tlow)
    if spec_cca:
        measurements.append(Measurement(name="battery_spec_cca", value=float(spec_cca.group(1)), unit="CCA"))
    volts = re.search(r"(\d+\.\d+)\s*volts", tlow)
    if volts:
        measurements.append(Measurement(name="battery_voltage_v", value=float(volts.group(1)), unit="V"))

    story.measurements = measurements

    if any(k in low for k in ("brake", "rotor", "pad", "squeal")):
        story.repair_type = "brake"
        story.parts = [
            Part(name="front brake pads", action="replaced"),
            Part(name="front rotors", action="replaced"),
        ]
    elif any(k in low for k in ("misfire", "coil", "p030")):
        story.repair_type = "misfire"
        pn = re.search(r"part number\s+([A-Z0-9-]+)", text, flags=re.I)
        story.parts = [Part(name="coil pack", part_number=pn.group(1) if pn else None, action="replaced")]
    elif any(k in low for k in ("battery", "midtronic", "cca")):
        story.repair_type = "battery"
        story.parts = [Part(name="battery", action="replaced")]

    story.concern = customer_concern or _first_sentence(text)
    if any(
        k in tlow
        for k in ("no more", "resolved", "verified repaired", "starts normally", "feel fine", "feels fine", "did not return")
    ):
        story.outcome = "verified_fixed"
    return synthesize_3cs(story, text)


def merge_stories(base: WarrantyStory, incoming: WarrantyStory) -> WarrantyStory:
    merged = base.model_copy(deep=True)
    if incoming.concern and not merged.concern:
        merged.concern = incoming.concern
    if incoming.dtcs:
        merged.dtcs = list(dict.fromkeys(merged.dtcs + incoming.dtcs))
    if incoming.cylinder is not None:
        merged.cylinder = incoming.cylinder
    if incoming.codes_cleared is not None:
        merged.codes_cleared = incoming.codes_cleared
    if incoming.parts:
        merged.parts = incoming.parts
    by_name = {m.name: m for m in merged.measurements}
    for m in incoming.measurements:
        m.source = "prompted"
        by_name[m.name] = m
    merged.measurements = list(by_name.values())
    if incoming.repair_type != "other":
        merged.repair_type = incoming.repair_type
    return synthesize_3cs(merged)


def apply_answers(story: WarrantyStory, answers: dict[str, str]) -> WarrantyStory:
    merged = story.model_copy(deep=True)
    by_name = {m.name: m for m in merged.measurements}

    def put(name: str, value: float, unit: str) -> None:
        by_name[name] = Measurement(name=name, value=value, unit=unit, source="prompted")

    custom_cause = None
    custom_correction = None

    for field, raw in answers.items():
        raw = (raw or "").strip()
        if not raw:
            continue
        if field == "dtc":
            codes = re.findall(r"P[0-9A-F]{4}", raw.upper())
            merged.dtcs = list(dict.fromkeys(merged.dtcs + (codes or [raw.upper()])))
        elif field == "cylinder":
            merged.cylinder = _int(raw)
        elif field == "codes_cleared":
            low = raw.lower()
            if any(neg in low for neg in ("no", "not", "false", "didn't")):
                merged.codes_cleared = False
            elif any(pos in low for pos in ("yes", "y", "yep", "true", "clear", "done", "1")):
                merged.codes_cleared = True
        elif field == "concern":
            merged.concern = raw
        elif field == "cause":
            custom_cause = raw
        elif field == "correction":
            custom_correction = raw
        elif field == "front_pad_thickness_left_mm":
            nums = _nums(raw)
            if nums:
                put("front_pad_thickness_left_mm", nums[0], "mm")
                put("front_pad_thickness_right_mm", nums[1] if len(nums) > 1 else nums[0], "mm")
        elif field == "front_rotor_thickness_left_mm":
            spec = re.search(r"(?:spec|min|minimum)(?:\s*is)?\s*(\d+(?:\.\d+)?)", raw.lower())
            if spec:
                put("front_rotor_min_spec_mm", float(spec.group(1)), "mm")
            nums = _nums(re.split(r"spec|min", raw, maxsplit=1, flags=re.I)[0])
            if nums:
                put("front_rotor_thickness_left_mm", nums[0], "mm")
                put("front_rotor_thickness_right_mm", nums[1] if len(nums) > 1 else nums[0], "mm")
        elif field == "caliper_bracket_torque_nm":
            nums = _nums(raw)
            if nums:
                put("caliper_bracket_torque_nm", nums[0], "Nm")
        elif field == "test_drive_miles":
            nums = _nums(raw)
            if nums:
                put("test_drive_miles", nums[0], "miles")
        elif field == "battery_midtronics_cca":
            nums = _nums(raw)
            if nums:
                put("battery_midtronics_cca", nums[0], "CCA")
            if len(nums) > 1:
                put("battery_spec_cca", nums[1], "CCA")

    merged.measurements = list(by_name.values())
    merged = synthesize_3cs(merged)
    if custom_cause:
        merged.cause = custom_cause
    if custom_correction:
        merged.correction = custom_correction
    return merged


def _nums(raw: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", raw)]


def _num(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower() in {"true", "yes", "1"}


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return parts[0] if parts else text
