"""Fenced jobs and immutable stage artifacts (independent queue branch)."""

from alembic import op

revision = "0006_proposal_jobs"
down_revision = "0002_local_models"
branch_labels = ("proposal_jobs",)
depends_on = None

DDL = """
CREATE TABLE worker_job (
  job_id text PRIMARY KEY,
  project_key text NOT NULL,
  operation_key text NOT NULL,
  kind text NOT NULL,
  input jsonb NOT NULL,
  status text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  lease_until timestamptz,
  worker_id text,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (project_key, operation_key),
  CHECK ((status = 'running') = (lease_until IS NOT NULL AND worker_id IS NOT NULL))
);
CREATE INDEX worker_job_claim ON worker_job(kind, available_at, created_at)
  WHERE status IN ('queued','running');
CREATE TABLE worker_stage_artifact (
  job_id text NOT NULL REFERENCES worker_job(job_id),
  stage text NOT NULL,
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  payload jsonb NOT NULL,
  generation bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (job_id, stage)
);
CREATE TABLE worker_application (
  job_id text PRIMARY KEY REFERENCES worker_job(job_id),
  generation bigint NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE worker_proposal_run (
  run_id text PRIMARY KEY,
  job_id text NOT NULL UNIQUE REFERENCES worker_job(job_id),
  project_key text NOT NULL,
  sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  record jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK ((record->>'approval_state' = 'proposed') IS TRUE),
  CHECK ((record->>'review_tier' = 'unreviewed') IS TRUE),
  CHECK ((record->>'scope_completeness' = 'unreviewed') IS TRUE),
  CHECK ((record->'training_eligible' = 'false'::jsonb) IS TRUE),
  CHECK ((record->'gold_eligible' = 'false'::jsonb) IS TRUE),
  CHECK (record ?& ARRAY['approval_state','review_tier','scope_completeness',
                         'training_eligible','gold_eligible'])
);
CREATE FUNCTION worker_record_immutable() RETURNS trigger
SET search_path FROM CURRENT AS $$
BEGIN
  RAISE EXCEPTION 'worker stage and application records are immutable';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER worker_input_immutable BEFORE UPDATE ON worker_job
FOR EACH ROW WHEN (OLD.job_id IS DISTINCT FROM NEW.job_id OR
  OLD.project_key IS DISTINCT FROM NEW.project_key OR
  OLD.operation_key IS DISTINCT FROM NEW.operation_key OR
  OLD.kind IS DISTINCT FROM NEW.kind OR OLD.input IS DISTINCT FROM NEW.input OR
  OLD.max_attempts IS DISTINCT FROM NEW.max_attempts)
EXECUTE FUNCTION worker_record_immutable();
"""


def upgrade() -> None:
    op.execute(DDL)
    for table in ("worker_stage_artifact", "worker_application", "worker_proposal_run"):
        op.execute(
            f"CREATE TRIGGER worker_no_update BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION worker_record_immutable()"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER worker_input_immutable ON worker_job")
    for table in ("worker_proposal_run", "worker_application", "worker_stage_artifact"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION worker_record_immutable()")
    op.execute("DROP TABLE worker_job")
