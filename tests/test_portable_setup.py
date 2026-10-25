import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from setup_project import configure_job

class PortableJobTests(unittest.TestCase):
    def test_job_paths_follow_folder_with_spaces_and_ampersand(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tests') as tmp:
            root = Path(tmp) / 'Project & data (test)'
            (root / 'pentaho').mkdir(parents=True)
            shutil.copy(ROOT / 'pentaho/ingestion.template.kjb', root / 'pentaho/ingestion.template.kjb')
            tree = ET.parse(configure_job(root))
            defaults = {p.findtext('name'): p.findtext('default_value') for p in tree.findall('./parameters/parameter')}
            self.assertEqual(defaults['CONFIG_PATH'], (root / 'config.json').as_posix())
            self.assertEqual(defaults['PYTHON_EXE'], (root / '.venv/Scripts/python.exe').as_posix())
            entry = next(e for e in tree.findall('./entries/entry') if e.findtext('name') == 'Python ingestion')
            self.assertEqual(entry.findtext('argument0'), (root / 'scripts/ingest.py').as_posix())
            self.assertEqual(entry.findtext('work_directory'), root.as_posix())
            self.assertEqual(entry.findtext('argument2'), '${CONFIG_PATH}')

