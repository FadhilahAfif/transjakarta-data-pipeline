import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ingest


class InputTests(unittest.TestCase):
    def config(self, **updates):
        value = {
            "host": "127.0.0.1", "port": 5432, "dbname": "transjakarta_oltp",
            "user": "postgres", "expected_rows": 189500
        }
        value.update(updates)
        return value

    def load(self, value):
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as folder:
            path = Path(folder) / "config.json"
            path.write_text(json.dumps(value), encoding="utf-8-sig")
            return ingest.load_config(path)

    def test_valid_config_accepts_utf8_bom(self):
        self.assertEqual(self.load(self.config())["expected_rows"], 189500)

    def test_password_preserved_exactly(self):
        password = ' sp:ace\\quote" '
        self.assertEqual(self.load(self.config(password=password))["password"], password)

    def test_invalid_password_rejected(self):
        for password in (None, 123, True, ""):
            with self.subTest(password=password), self.assertRaises(ValueError):
                self.load(self.config(password=password))

    def test_rejects_unknown_keys(self):
        for key in ("source_table", "unexpected"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load(self.config(**{key: "do-not-log-this"}))

    def test_rejects_nonpositive_or_noninteger_counts(self):
        for value in (0, -1, True, 1.5, "189500"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(self.config(expected_rows=value))

    def test_rejects_invalid_port(self):
        for value in (0, 65536, True, "5432"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(self.config(port=value))

    def test_rejects_missing_and_empty_fields(self):
        data = self.config()
        del data["host"]
        with self.assertRaises(ValueError):
            self.load(data)
        with self.assertRaises(ValueError):
            self.load(self.config(user=" "))

    def test_batch_id_cannot_be_sql_or_path(self):
        for value in ("", "../x", "x/y", "x' OR 1=1", "a b", "x" * 51):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ingest.validate_batch_id(value)

    def test_batch_id_accepts_expected_values(self):
        for value in ("M2_001", "batch-20261025", "x" * 50):
            self.assertEqual(ingest.validate_batch_id(value), value)

    def test_external_error_does_not_leak_value(self):
        self.assertNotIn("secret", ingest.error_summary(RuntimeError("secret")))

    def test_cli_missing_config_exits_nonzero_without_traceback(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts/ingest.py"),
             "--config", "missing-config.json", "--batch-id", "M2_001"],
            capture_output=True, text=True
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr + proc.stdout)


if __name__ == "__main__":
    unittest.main()


