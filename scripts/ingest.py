import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

# Nama tabel dan struktur kolom yang harus dipertahankan.
ROOT = Path(__file__).resolve().parents[1]
SOURCE = "transjakarta_oltp.transaksi_operasional"
LANDING = "transjakarta_landing.stg_transaksi_landing"
RUNS = "transjakarta_landing.ingestion_runs"
LOCK_ID = 205251025
SOURCE_COLUMNS = [
    ("transid", "character varying(50)"),
    ("paycardid", "bigint"),
    ("paycardbank", "character varying(50)"),
    ("paycardname", "character varying(100)"),
    ("paycardsex", "character varying(10)"),
    ("paycardbirthdate", "integer"),
    ("corridorid", "character varying(50)"),
    ("corridorname", "character varying(150)"),
    ("direction", "numeric(3,1)"),
    ("tapinstops", "character varying(50)"),
    ("tapinstopsname", "character varying(150)"),
    ("tapinstopslat", "double precision"),
    ("tapinstopslon", "double precision"),
    ("stopstartseq", "integer"),
    ("tapintime", "timestamp without time zone"),
    ("tapoutstops", "character varying(50)"),
    ("tapoutstopsname", "character varying(150)"),
    ("tapoutstopslat", "double precision"),
    ("tapoutstopslon", "double precision"),
    ("stopendseq", "numeric(5,1)"),
    ("tapouttime", "timestamp without time zone"),
    ("payamount", "numeric(10,2)"),
]
METADATA = [
    ("extraction_timestamp", "timestamp without time zone"),
    ("batch_id", "character varying(50)"),
    ("source_system", "character varying(50)"),
]
COLUMNS = ", ".join(name for name, _ in SOURCE_COLUMNS)


class IngestionError(ValueError):
    pass


# Validasi identitas batch dan konfigurasi koneksi.
def validate_batch_id(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", value):
        raise ValueError("batch_id harus 1-50 karakter huruf, angka, underscore, atau minus.")
    return value


def load_config(path):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    required = {"host", "port", "dbname", "user", "expected_rows"}
    if not isinstance(data, dict) or not required <= set(data) or set(data) - required - {"password"}:
        raise ValueError("Config wajib berisi host, port, dbname, user, expected_rows; password opsional.")
    for key in ("host", "dbname", "user"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"Config {key} harus berupa teks yang tidak kosong.")
    if type(data["port"]) is not int or not 1 <= data["port"] <= 65535:
        raise ValueError("Port harus bilangan bulat 1-65535.")
    if type(data["expected_rows"]) is not int or data["expected_rows"] <= 0:
        raise ValueError("expected_rows harus bilangan bulat positif.")
    if "password" in data and (not isinstance(data["password"], str) or not data["password"]):
        raise ValueError("Password harus berupa teks yang tidak kosong.")
    return data


# Batasi pesan error agar password dan isi data tidak masuk log.
def error_summary(error):
    if isinstance(error, IngestionError):
        return str(error)
    state = getattr(error, "sqlstate", None)
    return f"{type(error).__name__}" + (f" (SQLSTATE {state})" if state else "")


# Pastikan struktur sumber dan landing sesuai rancangan.
def check_schema(conn):
    for relation, expected in (
        (SOURCE, SOURCE_COLUMNS), (LANDING, SOURCE_COLUMNS + METADATA)
    ):
        actual = conn.execute(
            """SELECT attname, format_type(atttypid, atttypmod)
               FROM pg_attribute
               WHERE attrelid = to_regclass(%s) AND attnum > 0 AND NOT attisdropped
               ORDER BY attnum""", (relation,)
        ).fetchall()
        if actual != expected:
            raise IngestionError(f"Struktur {relation} tidak sesuai rancangan; periksa nama dan tipe kolom.")


# Periksa jumlah baris dan kelengkapan metadata batch.
def batch_stats(conn, batch_id):
    return conn.execute(
        f"""SELECT count(*),
                   count(*) FILTER (WHERE extraction_timestamp IS NULL
                     OR source_system IS DISTINCT FROM 'TRANSJAKARTA_OLTP'),
                   count(DISTINCT extraction_timestamp)
            FROM {LANDING} WHERE batch_id = %s""", (batch_id,)
    ).fetchone()


# Bandingkan seluruh nilai raw dalam dua arah, termasuk duplikat.
def raw_difference(conn, batch_id):
    return conn.execute(
        f"""SELECT count(*) FROM (
            (SELECT {COLUMNS} FROM {SOURCE}
             EXCEPT ALL SELECT {COLUMNS} FROM {LANDING} WHERE batch_id = %s)
            UNION ALL
            (SELECT {COLUMNS} FROM {LANDING} WHERE batch_id = %s
             EXCEPT ALL SELECT {COLUMNS} FROM {SOURCE})
        ) AS differences""", (batch_id, batch_id)
    ).fetchone()[0]


# Jalankan satu batch dengan lock agar tidak bertabrakan.
def ingest(conn, batch_id, expected_rows):
    validate_batch_id(batch_id)
    if not conn.autocommit:
        raise IngestionError("Koneksi ingestion harus autocommit sebelum memulai transaksi.")
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_ID,)).fetchone()[0]:
        raise IngestionError("Pipeline lain masih aktif. Coba lagi setelah selesai.")
    started = False
    try:
        check_schema(conn)
        previous = conn.execute(
            f"SELECT status, source_rows, landing_rows FROM {RUNS} WHERE batch_id=%s",
            (batch_id,)
        ).fetchone()
        # Batch sukses tidak dimuat ulang.
        if previous and previous[0] == "SUCCESS":
            rows, invalid, timestamps = batch_stats(conn, batch_id)
            if rows != previous[2] or invalid or timestamps != 1:
                raise IngestionError("Batch sukses tidak konsisten dengan audit; jangan memuat ulang otomatis.")
            return {"status": "ALREADY_SUCCESS", "batch_id": batch_id,
                    "source_rows": previous[1], "landing_rows": rows}

        if batch_stats(conn, batch_id)[0]:
            raise IngestionError("Batch belum sukses tetapi sudah berisi data. Periksa manual; data tidak dihapus.")

        conn.execute(
            f"""INSERT INTO {RUNS} (batch_id, status, started_at, source_relation)
                VALUES (%s, 'RUNNING', clock_timestamp(), %s)
                ON CONFLICT (batch_id) DO UPDATE SET
                  status='RUNNING', started_at=clock_timestamp(), finished_at=NULL,
                  source_rows=NULL, landing_rows=NULL, mismatch_rows=NULL, error_message=NULL,
                  source_relation=EXCLUDED.source_relation""", (batch_id, SOURCE)
        )
        # Salin dan validasi dalam satu transaksi; kegagalan membatalkan data.
        started = True
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.execute("SET LOCAL statement_timeout = '10min'")
            conn.execute("SET LOCAL lock_timeout = '10s'")
            source_rows = conn.execute(f"SELECT count(*) FROM {SOURCE}").fetchone()[0]
            if source_rows != expected_rows:
                raise IngestionError(f"Jumlah sumber {source_rows}; seharusnya {expected_rows}.")
            conn.execute(
                f"""INSERT INTO {LANDING}
                    ({COLUMNS}, extraction_timestamp, batch_id, source_system)
                    SELECT {COLUMNS}, transaction_timestamp() AT TIME ZONE 'Asia/Jakarta',
                           %s, 'TRANSJAKARTA_OLTP' FROM {SOURCE}""", (batch_id,)
            )
            # Commit hanya jika jumlah, metadata, dan nilai raw sesuai.
            rows, invalid, timestamps = batch_stats(conn, batch_id)
            mismatches = raw_difference(conn, batch_id)
            if rows != source_rows or invalid or timestamps != 1 or mismatches:
                raise IngestionError("Validasi jumlah, nilai raw, atau metadata gagal.")
            conn.execute(
                f"""UPDATE {RUNS} SET status='SUCCESS', finished_at=clock_timestamp(),
                    source_rows=%s, landing_rows=%s, mismatch_rows=%s
                    WHERE batch_id=%s""", (source_rows, rows, mismatches, batch_id)
            )
        return {"status": "SUCCESS", "batch_id": batch_id, "source_rows": source_rows,
                "landing_rows": rows, "mismatch_rows": mismatches}
    # Catat kegagalan setelah transaksi data dibatalkan.
    except BaseException as error:
        if started and not conn.closed:
            try:
                # SUCCESS dan data di-commit bersama; jangan timpa sukses saat respons commit terputus.
                conn.execute(
                    f"""UPDATE {RUNS} SET status='FAILED', finished_at=clock_timestamp(),
                        error_message=%s WHERE batch_id=%s AND status='RUNNING'""",
                    (error_summary(error), batch_id)
                )
            except Exception:
                logging.error("Status audit belum dapat diperbarui; periksa RUNNING sebelum retry.")
        raise
    finally:
        # Lepaskan lock setelah proses selesai.
        if not conn.closed:
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:
                logging.error("Pelepasan lock gagal; koneksi akan ditutup.")


# Baca argumen Spoon/CLI, buka koneksi, dan tulis ringkasan hasil.
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--batch-id", required=True)
    args = parser.parse_args()
    try:
        validate_batch_id(args.batch_id)
        config = load_config(args.config)
    except (ValueError, OSError) as error:
        print(f"CONFIG ERROR: {type(error).__name__}. Periksa path dan isi config.", file=sys.stderr)
        return 2

    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
            handlers=[logging.StreamHandler(sys.stdout),
                      logging.FileHandler(ROOT / "logs" / f"{args.batch_id}.log", encoding="utf-8")]
        )
        import psycopg
        expected = config.pop("expected_rows")
        start = time.monotonic()
        logging.info("START batch_id=%s expected_rows=%s", args.batch_id, expected)
        with psycopg.connect(**config, autocommit=True, connect_timeout=10,
                             application_name="transjakarta_m2") as conn:
            result = ingest(conn, args.batch_id, expected)
        result["duration_seconds"] = round(time.monotonic() - start, 3)
        logging.info(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        logging.error("FAILED batch_id=%s error=%s", args.batch_id, error_summary(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())

