CREATE TABLE IF NOT EXISTS rooftops (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dealer_group TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS repair_orders (
    id UUID PRIMARY KEY,
    rooftop_id TEXT NOT NULL REFERENCES rooftops (id),
    ro_number TEXT NOT NULL,
    vin TEXT NOT NULL,
    mileage INTEGER,
    labor_op TEXT,
    labor_op_name TEXT,
    vehicle_year INTEGER,
    vehicle_make TEXT,
    vehicle_model TEXT,
    advisor TEXT,
    technician TEXT,
    customer_concern TEXT,
    UNIQUE (rooftop_id, ro_number)
);

CREATE TABLE IF NOT EXISTS uploads (
    id UUID PRIMARY KEY,
    rooftop_id TEXT NOT NULL REFERENCES rooftops (id),
    content_hash TEXT NOT NULL,
    s3_bucket TEXT NOT NULL,
    s3_key TEXT NOT NULL,
    content_type TEXT,
    byte_size INTEGER,
    media_kind TEXT NOT NULL DEFAULT 'audio',
    metadata JSONB NOT NULL DEFAULT '{}',
    parent_upload_id UUID REFERENCES uploads (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (rooftop_id, content_hash)
);

CREATE TABLE IF NOT EXISTS repairs (
    id UUID PRIMARY KEY,
    rooftop_id TEXT NOT NULL REFERENCES rooftops (id),
    ro_number TEXT NOT NULL,
    vin TEXT NOT NULL,
    upload_id UUID NOT NULL REFERENCES uploads (id),
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingest_started_at TIMESTAMPTZ,
    ingest_finished_at TIMESTAMPTZ,
    filed_at TIMESTAMPTZ
);

ALTER TABLE uploads ADD COLUMN IF NOT EXISTS repair_id UUID REFERENCES repairs (id);
ALTER TABLE uploads ADD COLUMN IF NOT EXISTS media_kind TEXT NOT NULL DEFAULT 'audio';
ALTER TABLE uploads ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS repairs_status_idx ON repairs (status);
CREATE INDEX IF NOT EXISTS repairs_rooftop_created_idx ON repairs (rooftop_id, created_at DESC);
CREATE INDEX IF NOT EXISTS uploads_repair_idx ON uploads (repair_id);

CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    upload_id UUID REFERENCES uploads (id),
    repair_id UUID REFERENCES repairs (id),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    last_error TEXT,
    locked_at TIMESTAMPTZ,
    locked_by TEXT,
    run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS jobs_claim_idx ON jobs (status, run_after, created_at);

CREATE TABLE IF NOT EXISTS transcripts (
    id UUID PRIMARY KEY,
    repair_id UUID NOT NULL REFERENCES repairs (id),
    upload_id UUID NOT NULL REFERENCES uploads (id),
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS story_versions (
    id UUID PRIMARY KEY,
    repair_id UUID NOT NULL REFERENCES repairs (id),
    version INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    payload JSONB NOT NULL,
    missing_required TEXT[] NOT NULL DEFAULT '{}',
    followup_questions JSONB NOT NULL DEFAULT '[]',
    source_transcript_id UUID REFERENCES transcripts (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repair_id, version)
);

CREATE TABLE IF NOT EXISTS dms_writebacks (
    id UUID PRIMARY KEY,
    repair_id UUID NOT NULL REFERENCES repairs (id),
    idempotency_key TEXT NOT NULL UNIQUE,
    request_body JSONB,
    response_status INTEGER,
    response_body JSONB,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    repair_id UUID,
    upload_id UUID,
    job_id UUID,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    level TEXT NOT NULL DEFAULT 'info',
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS events_repair_idx ON events (repair_id, id);
