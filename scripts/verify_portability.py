"""Uji setup CSV dan ingestion pada database sementara milik pengujian."""
import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from ingest import ROOT, load_config, ingest, raw_difference, batch_stats
from setup_project import initialize_source


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--csv', type=Path, required=True)
    args = parser.parse_args()
    config = load_config(ROOT / 'config.json')
    expected = config.pop('expected_rows')
    test_name = 'transjakarta_m2_test_' + uuid.uuid4().hex[:12]
    test_config = {**config, 'dbname': test_name}
    created = False
    try:
        with psycopg.connect(**config, autocommit=True, connect_timeout=10) as admin:
            try:
                admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(test_name)))
                created = True
                env = os.environ.copy()
                env['M2_TEST_DSN'] = make_conninfo(**{k: v for k, v in test_config.items() if k != 'password'})
                if 'password' in test_config:
                    env['PGPASSWORD'] = test_config['password']
                result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'), '-v'], env=env)
                if result.returncode:
                    return result.returncode
                with psycopg.connect(**test_config, autocommit=True) as conn:
                    initialize_source(conn, args.csv, expected)
                    control = (ROOT / 'sql/01_ingestion_control.sql').read_text(encoding='utf-8-sig')
                    conn.execute('\n'.join(line for line in control.splitlines() if not line.startswith('\\')))
                    first = ingest(conn, 'PORTABILITY_TEST', expected)
                    retry = ingest(conn, 'PORTABILITY_TEST', expected)
                    assert first['status'] == 'SUCCESS' and first['mismatch_rows'] == 0
                    assert retry['status'] == 'ALREADY_SUCCESS'
                    assert batch_stats(conn, 'PORTABILITY_TEST') == (expected, 0, 1)
                    assert raw_difference(conn, 'PORTABILITY_TEST') == 0
                    print(json.dumps({'first': first, 'retry': retry}))
                print('PASS: setup CSV, ingestion penuh, validasi, dan retry pada database terpisah.')
            finally:
                if created:
                    admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(test_name)))
        return 0
    except Exception as error:
        print('Verification failed:', type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
