# Bay to Ground Truth

Status of the Bayworks data-infrastructure demo, plus remaining application work.

**Role:** Data Infrastructure Engineer — Bayworks (YC F26)  
**Hiring contact:** Shubham Goyal (CTO)  
**Date:** November 21, 2026

Related files: [`README.md`](README.md) (how to run) · [`DESIGN.md`](DESIGN.md) (scale to 1,000 rooftops)

---

## What was built

### Positioning
The prototype is framed for Shubham and the **Data Infrastructure Engineer** role, not as a generic voice-to-LLM wrapper. It follows Bayworks' live product loop ([bayworks.app](https://www.bayworks.app/)) and OEM warranty **3Cs** (Concern, Cause, Correction):

1. Technician records or uploads a repair note tied to a `rooftop_id`, `ro_number`, and `VIN`.
2. The device hashes bytes (`SHA-256`) and uploads via a **presigned S3 `PUT`** (proxy fallback if CORS fails).
3. Ingest is **idempotent on `(rooftop_id, content_hash)`** — the same file does not enqueue a second job.
4. A Postgres worker claims jobs with `FOR UPDATE SKIP LOCKED` and runs `transcribe → extract → validate → dms_sync`.
5. **Highlight:** deterministic warranty rules (not the LLM) block claims missing required measurements — pad/rotor thickness, torque, road-test miles, misfire DTC/cylinder, battery CCA — with status `needs_followup`.
6. Valid stories write back to a CDK-shaped mock DMS with an `Idempotency-Key`, retries, and a dead-letter path.

### Stack
Five Docker services (`docker-compose.yml`):

| Service | Image / process | Port |
|---|---|---|
| Postgres | `postgres:16-alpine` | 5433 |
| Object store | LocalStack S3 (`localstack/localstack:3.8`) | 4566 |
| Mock DMS | FastAPI; ROs `24-1009`, `24-1088`, `24-1102` | 8001 |
| API + UI | FastAPI; bay `/` and ops `/ops` | 8000 |
| Worker | `python -m app.worker` | — |

Local notes: Colima is the Docker daemon (`colima start`). This machine uses the standalone `docker-compose` binary, not the `docker compose` plugin. MinIO was dropped after Hub/Quay blocked unauthenticated pulls.

Five fixtures live in `fixtures/transcripts/` (`brake_complete`, `brake_incomplete`, `misfire_complete`, `misfire_incomplete`, `battery_complete`). Heuristic extraction plus optional Groq/OpenAI means the demo runs with or without API keys.

### Audit fixes already on disk
A code review found ten issues. Backend, `app.css`, `tech.html`, and (this session) `tech.js` / ops UI / tests are on disk.

1. **`synthesize_3cs()`** — Cause/Correction now include spoken and prompted measurements instead of echoing the raw incomplete transcript.
2. **Pipeline latency** — `ingest_finished_at` is stamped once at first decision, so typing follow-up answers does not inflate `avg_latency_s`.
3. **Missing-field rate** — Ops metrics use story v1 (`caught_missing_fields / validated_repairs`) so answering a prompt does not zero the catch rate.
4. **`ensure_rooftop()`** — A custom rooftop ID creates the tenant and demo ROs instead of a foreign-key 500.
5. **Startup locks** — `pg_advisory_xact_lock` so API and worker cannot race on DDL or seed.
6. **`PermanentJobError`** — Live audio with no STT key goes straight to `dead_letter` instead of five slow retries.
7. **Reset and DMS faults** — `POST /api/ops/reset`, `fail-next` with a count, versioned DMS idempotency keys (`…:v{story_version}`).
8. **Evidence uploads** — `uploads.repair_id`, `media_kind`, `metadata`, and `POST /api/repairs/{id}/evidence`.
9. **Follow-up parsing** — Rotor `spec`/`min` split, broader `codes_cleared` matching, reject blank answers.
10. **Bay UI markup** — Audio file input, video/photo attach, ground-truth chips, job-error and flash banners in `tech.html` / `app.css`.

---

## Done this session (21 Nov)

- [x] **Wired `app/static/tech.js` to `tech.html`**
  - Idempotent-replay banner sticks after poll (`state.sawDuplicate`).
  - Measurement chips (`spoken` / `prompted`), parts, DTCs, cylinder, codes-cleared, `last_error`.
  - Audio-file upload (presigned PUT + proxy) and video/photo evidence (presigned PUT, canvas keyframe, `POST …/evidence`).
  - Mic permission errors go to `#flash`; follow-up inputs are not rebuilt mid-type; strings are HTML-escaped.
- [x] **Updated `app/static/ops.html` and `ops.js`**
  - Buttons: fail 1 DMS write (retry), fail 5 (dead letter), reset demo. Inline `#ops-status`, no `alert()`.
  - Five metric cards: Repairs, Filed, Caught missing fields, Dead-letter, Avg latency. Event-log HTML escaped.
- [x] **Expanded `tests/test_validate.py`** — 8 passed (`docker-compose exec -T api pytest -q`).
- [x] **Rebuilt and smoke-tested** (`docker-compose up --build -d`)
  - Reset, all five fixtures, brake follow-up files as story v2, idempotent replay, 1-fault retry (`attempts=2` then filed), 5-fault DLQ (`dead_letter`, 5 attempts).
  - Live DB after smoke: 6 repairs, 4 filed, 2 caught-missing (v1), 1 dead-letter, 1 photo evidence row.

## Pending

### Application and outreach

- [ ] Confirm work authorization against the posting’s “US citizen/visa only” line before more build time.
- [ ] Resume: filename says 2027, body says Dec 2026; surface SpotDraft NER → knowledge graph; add an LLM-eval metric; expand voice AI if the work is real.
- [ ] Optional live capture: `GROQ_API_KEY` or `OPENAI_API_KEY` in `.env`, one complete brake note, one incomplete, one short video.
- [ ] Git init, commit (keep `.env` out), push to GitHub.
- [ ] Two-minute Loom: architecture → complete brake → missing-measurement block and v2 file → idempotent replay + DMS retry/DLQ → `DESIGN.md`.
- [ ] Email `founders@bayworks.app` (Kaustubh and Shubham) Tuesday or Wednesday morning PT with repo + Loom; LinkedIn DM backup; one follow-up after a week.
- [ ] Interview: draw the pipeline from memory in 30 minutes with one tradeoff per step; three 2-minute stories (ingestion, data-quality catch, outage/rollback).
