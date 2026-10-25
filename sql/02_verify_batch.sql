\set ON_ERROR_STOP on
\if :{?batch_id}
\else
\set batch_id M2_001
\endif
SELECT batch_id, status, started_at, finished_at, source_rows, landing_rows, mismatch_rows
FROM transjakarta_landing.ingestion_runs WHERE batch_id = :'batch_id';
SELECT count(*) AS landing_rows, count(DISTINCT extraction_timestamp) AS extraction_times,
 count(*) FILTER (WHERE extraction_timestamp IS NULL
 OR source_system IS DISTINCT FROM 'TRANSJAKARTA_OLTP') AS invalid_metadata
FROM transjakarta_landing.stg_transaksi_landing WHERE batch_id = :'batch_id';
SELECT transid, extraction_timestamp, batch_id, source_system
FROM transjakarta_landing.stg_transaksi_landing
WHERE batch_id = :'batch_id' ORDER BY transid LIMIT 10;
-- Perbandingan ini memakai kondisi sumber saat kueri dijalankan, bukan snapshot historis.
SELECT count(*) AS differences_against_current_source FROM (
 (SELECT to_jsonb(s) AS raw FROM transjakarta_oltp.transaksi_operasional s
  EXCEPT ALL
  SELECT to_jsonb(l) - ARRAY['extraction_timestamp','batch_id','source_system']
  FROM transjakarta_landing.stg_transaksi_landing l WHERE batch_id = :'batch_id')
 UNION ALL
 (SELECT to_jsonb(l) - ARRAY['extraction_timestamp','batch_id','source_system']
  FROM transjakarta_landing.stg_transaksi_landing l WHERE batch_id = :'batch_id'
  EXCEPT ALL
  SELECT to_jsonb(s) FROM transjakarta_oltp.transaksi_operasional s)
) d;

