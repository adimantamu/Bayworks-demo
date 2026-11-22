# Bayworks demo — bay to ground truth

Data-infrastructure slice for Bayworks: a technician voice note becomes a validated OEM 3Cs warranty story, or it gets blocked for a missing measurement **before** it is filed to the DMS.

This is the pipeline, not a chatbot. Transcription and the LLM are two workers. The product is idempotent ingest, versioned records, a fail-closed validator, and a DMS adapter you do not control.

## What to show (2 minutes)

1. Run **Brake · complete**. Stages flip. A 3Cs story files to the mock DMS. Lineage shows `s3://…` + SHA-256 + `prompt_version` + `validator_version`.
2. Run **Brake · missing measurements**. Status is `needs_followup`. Ops missing-field rate goes up. The claim does **not** file. Fill pad/rotor/torque/test-drive and it files as story v2.
3. Re-run the same complete fixture. Same content hash → no new job.
4. On `/ops`, **Break next DMS write**, then run a complete scenario. Worker retries; inject enough failures and it dead-letters.

Phone capture is on `/` (presigned S3 PUT, proxy fallback). Live mic needs `GROQ_API_KEY` or `OPENAI_API_KEY`. Fixtures do not.

## Run

```bash
cp .env.example .env
docker-compose up --build
```

This machine uses the `docker-compose` binary (the `docker compose` plugin is not installed). If the daemon is down: `colima start`.

- Landing: http://localhost:8000
- Technician bay: http://localhost:8000/bay
- Ops: http://localhost:8000/ops
- Mock DMS: http://localhost:8001/repair-orders/24-1009
- LocalStack S3: http://localhost:4566

Live demo (Cloudflare tunnel in front of the EC2 API): https://affect-festivals-zone-comply.trycloudflare.com

That URL is a Cloudflare quick tunnel. If the instance or `cloudflared` restarts, read the new URL with `journalctl -u cloudflared | grep trycloudflare.com`. The EC2 box runs Postgres, an S3-compatible store with dummy MP4/MPS objects, and the FastAPI app.

Optional in `.env`:

```
GROQ_API_KEY=...    # free Whisper + Llama
OPENAI_API_KEY=...
```

## Layout

```
app/            API, worker, validator, S3, DMS client
mock_dms/       CDK-shaped API with Idempotency-Key + fault injection
fixtures/       3Cs transcripts (complete + deliberately incomplete)
schema.sql      Postgres source of truth
DESIGN.md       1,000-rooftop scale notes
```

Queue is a `jobs` table claimed with `FOR UPDATE SKIP LOCKED`. Object store is LocalStack S3. Idempotency key is `(rooftop_id, content_hash)`.

## Tests

```bash
docker-compose exec api pytest -q
```
