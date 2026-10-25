\set ON_ERROR_STOP on
BEGIN;
DO $$ BEGIN
 IF current_database() <> 'transjakarta_oltp'
    AND current_database() NOT LIKE 'transjakarta_m2_test%' THEN
   RAISE EXCEPTION 'Gunakan database transjakarta_oltp atau database khusus uji.';
 END IF;
 IF to_regclass('transjakarta_landing.stg_transaksi_landing') IS NULL THEN
   RAISE EXCEPTION 'Tabel landing belum tersedia.';
 END IF;
END $$;
SELECT pg_advisory_xact_lock(205251025);
CREATE TABLE IF NOT EXISTS transjakarta_landing.ingestion_runs (
 batch_id VARCHAR(50) PRIMARY KEY,
 status VARCHAR(10) NOT NULL CHECK (status IN ('RUNNING','SUCCESS','FAILED')),
 started_at TIMESTAMPTZ NOT NULL,
 finished_at TIMESTAMPTZ,
 source_rows BIGINT,
 landing_rows BIGINT,
 mismatch_rows BIGINT,
 source_relation TEXT NOT NULL,
 error_message TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_landing_batch_transid
 ON transjakarta_landing.stg_transaksi_landing(batch_id, transid);
COMMIT;
SELECT batch_id, status, source_rows, landing_rows FROM transjakarta_landing.ingestion_runs;

