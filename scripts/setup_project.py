import argparse
import csv
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from ingest import ROOT, SOURCE, LANDING, SOURCE_COLUMNS, METADATA, load_config, error_summary


def configure_job(root=ROOT):
    # Buat path biasa agar Spoon tidak meneruskan URL file:///.
    root = Path(root).resolve()
    tree = ET.parse(root / 'pentaho/ingestion.template.kjb')
    defaults = {'CONFIG_PATH': (root / 'config.json').as_posix(),
                'PYTHON_EXE': (root / '.venv/Scripts/python.exe').as_posix()}
    for parameter in tree.findall('./parameters/parameter'):
        if parameter.findtext('name') in defaults:
            parameter.find('default_value').text = defaults[parameter.findtext('name')]
    entry = next(e for e in tree.findall('./entries/entry') if e.findtext('name') == 'Python ingestion')
    entry.find('argument0').text = (root / 'scripts/ingest.py').as_posix()
    entry.find('work_directory').text = root.as_posix()
    output = root / 'pentaho/ingestion.kjb'
    tree.write(output, encoding='utf-8', xml_declaration=True)
    return output


def initialize_source(conn, csv_path, expected_rows):
    # Setup pertama tidak menimpa atau mengosongkan tabel lama.
    name = conn.execute('SELECT current_database()').fetchone()[0]
    if name != 'transjakarta_oltp' and not name.startswith('transjakarta_m2_test'):
        raise ValueError('Gunakan database transjakarta_oltp atau database khusus uji.')
    with open(csv_path, encoding='utf-8-sig', newline='') as source:
        header = next(csv.reader(source))
        if [name.lower() for name in header] != [name for name, _ in SOURCE_COLUMNS]:
            raise ValueError('Header CSV tidak sesuai 22 kolom sumber.')
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(205251025)')
            if any(conn.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0]
                   for table in (SOURCE, LANDING)):
                raise ValueError('Tabel sumber/landing sudah ada. Setup awal dibatalkan.')
            conn.execute('CREATE SCHEMA IF NOT EXISTS transjakarta_oltp')
            conn.execute('CREATE SCHEMA IF NOT EXISTS transjakarta_landing')
            columns = ', '.join(f'{name} {kind}' for name, kind in SOURCE_COLUMNS)
            conn.execute(f'CREATE TABLE {SOURCE} ({columns}, PRIMARY KEY (transid))')
            metadata = ', '.join(f'{name} {kind}' for name, kind in METADATA)
            conn.execute(f'CREATE TABLE {LANDING} (LIKE {SOURCE}, {metadata})')
            with conn.cursor().copy(f'COPY {SOURCE} FROM STDIN WITH (FORMAT CSV)') as copy:
                while chunk := source.read(1024 * 1024):
                    copy.write(chunk)
            count = conn.execute(f'SELECT count(*) FROM {SOURCE}').fetchone()[0]
            if count != expected_rows:
                raise ValueError(f'Jumlah CSV {count}; diharapkan {expected_rows}. Setup dibatalkan.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--initialize-source', type=Path)
    parser.add_argument('--job-only', action='store_true')
    args = parser.parse_args()
    try:
        if not args.job_only:
            import psycopg
            config = load_config(ROOT / 'config.json')
            expected = config.pop('expected_rows')
            with psycopg.connect(**config, autocommit=True, connect_timeout=10) as conn:
                if args.initialize_source:
                    initialize_source(conn, args.initialize_source, expected)
                sql = (ROOT / 'sql/01_ingestion_control.sql').read_text(encoding='utf-8-sig')
                conn.execute('\n'.join(line for line in sql.splitlines() if not line.startswith('\\')))
        configure_job()
        print('Persiapan selesai. Buka pentaho/ingestion.kjb di Spoon.')
        return 0
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else error_summary(error)
        print(f'SETUP FAILED: {message}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

