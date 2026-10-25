import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import ingest
from setup_project import initialize_source

@unittest.skipUnless(os.environ.get('M2_TEST_DSN'), 'Set M2_TEST_DSN ke database uji kosong.')
class SetupDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(os.environ['M2_TEST_DSN'], autocommit=True)
        self.addCleanup(self.conn.close)
        name = self.conn.execute('SELECT current_database()').fetchone()[0]
        if not name.startswith('transjakarta_m2_test'):
            raise RuntimeError('Setup tests hanya boleh memakai database uji.')
        if self.conn.execute("SELECT 1 FROM pg_namespace WHERE nspname IN ('transjakarta_oltp','transjakarta_landing')").fetchone():
            raise RuntimeError('Gunakan database uji kosong; schema lama tidak dihapus.')
        self.addCleanup(self.clean_created_schemas)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tests')
        self.addCleanup(self.temp.cleanup)
        self.csv = Path(self.temp.name) / 'source.csv'
        with self.csv.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow([name for name, _ in ingest.SOURCE_COLUMNS])
            writer.writerows([[key] + [''] * 21 for key in ('T1', 'T2')])

    def clean_created_schemas(self):
        self.conn.execute('DROP SCHEMA IF EXISTS transjakarta_oltp CASCADE; DROP SCHEMA IF EXISTS transjakarta_landing CASCADE')

    def test_initialize_creates_source_and_empty_landing(self):
        initialize_source(self.conn, self.csv, 2)
        self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {ingest.SOURCE}').fetchone()[0], 2)
        self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {ingest.LANDING}').fetchone()[0], 0)
        ingest.check_schema(self.conn)

    def test_wrong_count_rolls_back_schema_and_rows(self):
        with self.assertRaises(ValueError):
            initialize_source(self.conn, self.csv, 3)
        self.assertIsNone(self.conn.execute('SELECT to_regclass(%s)', (ingest.SOURCE,)).fetchone()[0])

    def test_second_initialization_keeps_existing_rows(self):
        initialize_source(self.conn, self.csv, 2)
        with self.assertRaises(ValueError):
            initialize_source(self.conn, self.csv, 2)
        self.assertEqual(self.conn.execute(f'SELECT count(*) FROM {ingest.SOURCE}').fetchone()[0], 2)

    def test_wrong_header_creates_no_table(self):
        self.csv.write_text('wrong\nvalue\n', encoding='utf-8')
        with self.assertRaises(ValueError):
            initialize_source(self.conn, self.csv, 1)
        self.assertIsNone(self.conn.execute('SELECT to_regclass(%s)', (ingest.SOURCE,)).fetchone()[0])
