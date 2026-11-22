# Bay → ground truth at 1,000 rooftops

This demo is the bay-to-warranty slice: capture, idempotent ingest, transcribe, extract, **validate**, DMS writeback. Scaling it is a data problem, not a model problem.

## Tenancy

`rooftop_id` is the partition key on every object prefix, job, and repair row. A dealer group is a parent for billing and rollups, never the isolation unit. Warranty audits, access control, and backfills all run per rooftop so one noisy shop cannot starve the others.

At 1,000 rooftops / ~4,000 repairs a day, Postgres stays the system of record if we hash-partition `repairs`, `jobs`, and `events` by `rooftop_id` (or by a rooftop shard map). S3 prefixes already look like `{rooftop}/repairs/{ro}/{sha}`.

## Capture over bad shop Wi-Fi

Voice notes are small; video is not. Presigned PUT is enough for audio. Video should use multipart + resumable chunks with an on-device queue. The browser computes SHA-256 before upload; `(rooftop_id, content_hash)` is the idempotency key. Replaying a dropped connection must not create a second repair.

## Processing graph

`jobs` is claimed with `FOR UPDATE SKIP LOCKED`. That is the right queue until we are multi-region or the fan-out includes long video. Then the same job row becomes the outbox onto SQS/Kafka, keyed by rooftop.

Stages are separate on purpose:

1. Transcribe (Whisper) — expensive, retryable, immutable transcript row
2. Extract (LLM, `prompt_version`) — pure function of transcript + schema
3. Validate (deterministic OEM rules, `validator_version`) — **not** the LLM
4. DMS sync — adapter with `Idempotency-Key`

Raw audio is never mutated. A prompt change is a new `story_versions` row, not an UPDATE. Reprocessing is `SELECT audio FROM objects WHERE rooftop IN (...)` and enqueue extract jobs with the new `prompt_version`. Warranty admins can still open v1 of the story that was actually filed.

## DMS we do not own

CDK / Reynolds / Tekion sit behind one interface. Their outage is our `filing` state plus retries, not data loss. Writebacks go through an outbox (`dms_writebacks`) with an idempotency key of `{rooftop}:{ro}:{content_hash}`. Duplicate posts must be no-ops on their side and on ours.

## Quality before the dealer

The SLO is **missing-field rate**, not BLEU. A claim that leaves the bay without pad/rotor measurements, torque, or a DTC is a chargeback waiting to happen. Rules are keyed by labor op / repair type and fail closed. Alert when a rooftop’s block rate spikes: bad mic, new labor op with no rule pack, or a broken prompt.

## Storage cost

4,000 audio notes/day is cheap. Video is the cost bomb. Keep audio hot. Keep video 90 days in standard, then glacier; store keyframes + metadata for search and evals. Customer PII lives on the RO in the DMS; we store VIN, mileage, and tech id with retention matching the OEM audit window, encrypted at rest, prefixed so a rooftop offboard deletes a prefix.

## What I would add after week one

SQS/Kafka for the ingest topic, KMS and per-rooftop customer-managed keys, OpenLineage from job → transcript → story version → DMS id, and an OEM rule pack (Ford 3Cs + measurements, GM brake form, Honda OP codes) loaded as data, not code.
