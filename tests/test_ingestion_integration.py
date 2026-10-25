import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest

TEST_DSN = os.environ.get("M2_TEST_DSN")


@unittest.skipUnless(TEST_DSN, "Set M2_TEST_DSN ke database khusus transjakarta_m2_test.")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.conn = psycopg.connect(TEST_DSN, autocommit=True)
        name = cls.conn.execute("SELECT current_database()").fetchone()[0]
        if not name.startswith("transjakarta_m2_test"):
            cls.conn.close()
            raise RuntimeError("Tes hanya boleh memakai database transjakarta_m2_test*.")
        existing = cls.conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname IN ('transjakarta_oltp','transjakarta_landing')"
        ).fetchone()
        if existing:
            cls.conn.close()
            raise RuntimeError("Gunakan database uji kosong; schema yang sudah ada tidak dihapus.")
        with cls.conn.transaction():
            cls.conn.execute("CREATE SCHEMA transjakarta_oltp; CREATE SCHEMA transjakarta_landing")
            columns = ", ".join(f"{name} {kind}" for name, kind in ingest.SOURCE_COLUMNS)
            cls.conn.execute(f"CREATE TABLE {ingest.SOURCE} ({columns}, PRIMARY KEY (transid))")
            cls.conn.execute(
                f"""CREATE TABLE {ingest.LANDING} (
                    LIKE {ingest.SOURCE},
                    extraction_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    batch_id VARCHAR(50),
                    source_system VARCHAR(50) DEFAULT 'TRANSJAKARTA_OLTP')"""
            )
        sql = (ROOT / "sql/01_ingestion_control.sql").read_text(encoding="utf-8-sig")
        sql = "\n".join(line for line in sql.splitlines() if not line.startswith("\\"))
        cls.conn.execute(sql)

    @classmethod
    def tearDownClass(cls):
        cls.conn.execute("DROP SCHEMA transjakarta_oltp CASCADE; DROP SCHEMA transjakarta_landing CASCADE")
        cls.conn.close()

    def setUp(self):
        self.conn.execute(f"TRUNCATE {ingest.SOURCE}, {ingest.LANDING}, {ingest.RUNS}")
        self.conn.execute(
            f"""INSERT INTO {ingest.SOURCE}
                (transid,paycardid,paycardname,payamount,tapintime,tapouttime,tapinstopslat)
                VALUES
                ('T1',123456789012345,'Nama uji é',0,'2023-04-01 08:00',NULL,-6.123456789),
                ('T2',NULL,NULL,3500,'2023-04-01 09:00','2023-04-01 10:00',NULL),
                ('T3',3,'Uji',20000,NULL,NULL,0)"""
        )

    def count(self, batch):
        return self.conn.execute(
            f"SELECT count(*) FROM {ingest.LANDING} WHERE batch_id=%s", (batch,)
        ).fetchone()[0]

    def test_raw_null_zero_and_metadata_preserved(self):
        result = ingest.ingest(self.conn, "TEST_OK", 3)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["mismatch_rows"], 0)
        self.assertEqual(ingest.raw_difference(self.conn, "TEST_OK"), 0)
        self.assertEqual(ingest.batch_stats(self.conn, "TEST_OK"), (3, 0, 1))
        raw = self.conn.execute(
            f"SELECT payamount,tapouttime FROM {ingest.LANDING} WHERE transid='T1'"
        ).fetchone()
        self.assertEqual(raw[0], 0)
        self.assertIsNone(raw[1])

    def test_successful_retry_does_not_duplicate(self):
        ingest.ingest(self.conn, "TEST_RETRY", 3)
        result = ingest.ingest(self.conn, "TEST_RETRY", 3)
        self.assertEqual(result["status"], "ALREADY_SUCCESS")
        self.assertEqual(self.count("TEST_RETRY"), 3)

    def test_new_snapshot_is_separate(self):
        ingest.ingest(self.conn, "TEST_ONE", 3)
        self.conn.execute(f"UPDATE {ingest.SOURCE} SET payamount=3500 WHERE transid='T1'")
        ingest.ingest(self.conn, "TEST_TWO", 3)
        self.assertEqual(self.count("TEST_ONE"), 3)
        self.assertEqual(self.count("TEST_TWO"), 3)
        self.assertEqual(ingest.raw_difference(self.conn, "TEST_TWO"), 0)

    def test_wrong_count_rolls_back_then_retry_succeeds(self):
        with self.assertRaises(ingest.IngestionError):
            ingest.ingest(self.conn, "TEST_COUNT", 4)
        self.assertEqual(self.count("TEST_COUNT"), 0)
        self.assertEqual(self.conn.execute(
            f"SELECT status FROM {ingest.RUNS} WHERE batch_id='TEST_COUNT'"
        ).fetchone()[0], "FAILED")
        self.assertEqual(ingest.ingest(self.conn, "TEST_COUNT", 3)["status"], "SUCCESS")

    def test_empty_source_is_not_published(self):
        self.conn.execute(f"TRUNCATE {ingest.SOURCE}")
        with self.assertRaises(ingest.IngestionError):
            ingest.ingest(self.conn, "TEST_EMPTY", 3)
        self.assertEqual(self.count("TEST_EMPTY"), 0)

    def test_failure_after_insert_rolls_back_all_rows(self):
        self.conn.execute("""
            CREATE FUNCTION transjakarta_landing.reject_success() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN
                IF NEW.status='SUCCESS' THEN RAISE EXCEPTION 'test failure'; END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER reject_success BEFORE UPDATE ON transjakarta_landing.ingestion_runs
            FOR EACH ROW EXECUTE FUNCTION transjakarta_landing.reject_success()
        """)
        try:
            with self.assertRaises(self.psycopg.Error):
                ingest.ingest(self.conn, "TEST_ROLLBACK", 3)
            self.assertEqual(self.count("TEST_ROLLBACK"), 0)
            self.assertEqual(self.conn.execute(
                f"SELECT status FROM {ingest.RUNS} WHERE batch_id='TEST_ROLLBACK'"
            ).fetchone()[0], "FAILED")
        finally:
            self.conn.execute(
                "DROP TRIGGER reject_success ON transjakarta_landing.ingestion_runs; "
                "DROP FUNCTION transjakarta_landing.reject_success()"
            )

    def test_modified_raw_is_rejected_before_commit(self):
        self.conn.execute("""
            CREATE FUNCTION transjakarta_landing.corrupt_test() RETURNS trigger
            LANGUAGE plpgsql AS $$ BEGIN NEW.payamount=99; RETURN NEW; END $$;
            CREATE TRIGGER corrupt_test BEFORE INSERT ON transjakarta_landing.stg_transaksi_landing
            FOR EACH ROW EXECUTE FUNCTION transjakarta_landing.corrupt_test()
        """)
        try:
            with self.assertRaises(ingest.IngestionError):
                ingest.ingest(self.conn, "TEST_RAW", 3)
            self.assertEqual(self.count("TEST_RAW"), 0)
        finally:
            self.conn.execute(
                "DROP TRIGGER corrupt_test ON transjakarta_landing.stg_transaksi_landing; "
                "DROP FUNCTION transjakarta_landing.corrupt_test()"
            )

    def test_concurrent_run_is_rejected(self):
        with self.psycopg.connect(TEST_DSN, autocommit=True) as other:
            other.execute("SELECT pg_advisory_lock(%s)", (ingest.LOCK_ID,))
            with self.assertRaises(ingest.IngestionError):
                ingest.ingest(self.conn, "TEST_LOCK", 3)
            self.assertEqual(self.count("TEST_LOCK"), 0)

    def test_schema_drift_rejected_without_insert(self):
        self.conn.execute(f"ALTER TABLE {ingest.SOURCE} ADD COLUMN unexpected TEXT")
        try:
            with self.assertRaises(ingest.IngestionError):
                ingest.ingest(self.conn, "TEST_SCHEMA", 3)
            self.assertEqual(self.count("TEST_SCHEMA"), 0)
        finally:
            self.conn.execute(f"ALTER TABLE {ingest.SOURCE} DROP COLUMN unexpected")

    def test_stale_running_can_retry_after_session_lock_released(self):
        self.conn.execute(
            f"INSERT INTO {ingest.RUNS}(batch_id,status,started_at,source_relation) "
            "VALUES ('TEST_STALE','RUNNING',now(),%s)", (ingest.SOURCE,)
        )
        self.assertEqual(ingest.ingest(self.conn, "TEST_STALE", 3)["status"], "SUCCESS")

    def test_orphan_rows_are_not_deleted(self):
        self.conn.execute(
            f"INSERT INTO {ingest.LANDING} (transid,batch_id) VALUES ('orphan','TEST_ORPHAN')"
        )
        with self.assertRaises(ingest.IngestionError):
            ingest.ingest(self.conn, "TEST_ORPHAN", 3)
        self.assertEqual(self.count("TEST_ORPHAN"), 1)


if __name__ == "__main__":
    unittest.main()

