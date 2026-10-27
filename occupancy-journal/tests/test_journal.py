import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("journal", Path(__file__).parents[1] / "journal.py")
journal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(journal)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        journal.DATA = Path(self.tmp.name)
        journal.DB = journal.DATA / "journal.sqlite3"
        journal.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_privacy_scope_and_unknown(self):
        result = journal.select_states([
            {"entity_id": "person.example", "state": "unknown", "attributes": {
                "latitude": 12, "longitude": 34, "user_id": "private",
                "source": "device_tracker.example"}},
            {"entity_id": "light.example", "state": "on", "attributes": {}},
        ])
        self.assertNotIn("light.example", result)
        self.assertEqual(result["person.example"]["state"], "unknown")
        self.assertEqual(result["person.example"]["attributes"], {"source": "device_tracker.example"})

    def test_revisions_and_restart_preserve_prior_forecast(self):
        first = {"predicted_at": "first", "eta_minutes": 20}
        second = {"predicted_at": "second", "eta_minutes": 18}
        with journal.connect() as db:
            self.assertTrue(journal.append(db, "forecast", first, "2026-01-01T00:00:00+00:00", True))
            self.assertFalse(journal.append(db, "forecast", first, "2026-01-01T00:01:00+00:00", True))
            self.assertTrue(journal.append(db, "forecast", second, "2026-01-01T00:02:00+00:00", True))
        journal.initialize()
        with journal.connect() as db:
            rows = db.execute("SELECT payload FROM records ORDER BY id").fetchall()
        self.assertEqual([json.loads(gzip.decompress(row[0]))["eta_minutes"] for row in rows], [20, 18])

    def test_source_failure_recovery_and_retention(self):
        with journal.connect() as db:
            journal.append(db, "observations", {}, "2000-01-01T00:00:00+00:00")
        with patch.object(journal, "get_json", side_effect=TimeoutError("private exception details")):
            journal.sample()
        self.assertIn("forecast", journal.RUNTIME["errors"])
        with patch.object(journal, "get_json", side_effect=lambda url, auth=False: [] if auth else {"available": False}):
            journal.sample()
        self.assertEqual(journal.RUNTIME["errors"], {})
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM records WHERE kind='collection_health'").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM records WHERE captured_at<'2020'").fetchone()[0], 0)
            texts = [gzip.decompress(r[0]).decode() for r in db.execute("SELECT payload FROM records")]
            self.assertFalse(any("private exception details" in text for text in texts))

    def test_failed_transaction_is_rolled_back(self):
        with self.assertRaises(RuntimeError):
            with journal.connect() as db:
                journal.append(db, "forecast", {"p": .7}, journal.now())
                raise RuntimeError("abort")
        with journal.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)

    def test_coverage_and_tracker_disagreement_ignore_unknown_trackers(self):
        observation = {"person.example": {"state": "home", "attributes": {
            "device_trackers": ["device_tracker.a", "device_tracker.b", "device_tracker.c"]}},
            "device_tracker.a": {"state": "home"},
            "device_tracker.b": {"state": "not_home"},
            "device_tracker.c": {"state": "unknown"}}
        with journal.connect() as db:
            journal.append(db, "observations", observation, journal.now())
        with patch.object(journal, "RESIDENTS", {"Example": "person.example"}):
            result = journal.status()
        self.assertEqual(result["observation_history"]["samples_last_24h"], 1)
        self.assertEqual(result["observation_history"]["calendar_days"], 1)
        self.assertEqual(result["tracker_checks"]["Example"], {
            "available": 2, "disagree": True, "entity_present": True})

    def test_panel_assets_keep_ingress_access_boundary(self):
        outputs = []
        handler = SimpleNamespace(path="/assets/journal.js", client_address=("192.0.2.10", 1234),
                                  send=lambda *args: outputs.append(args))
        journal.Handler.do_GET(handler)
        self.assertEqual(outputs[0][0], 403)
        self.assertNotIn("const $", str(outputs))

    def test_panel_assets_have_correct_types_and_no_path_traversal(self):
        outputs = []
        handler = SimpleNamespace(path="/assets/journal.js", client_address=("127.0.0.1", 1234),
                                  send=lambda *args: outputs.append(args))
        app = Path(__file__).parents[1]
        with patch.object(journal, "Path", side_effect=lambda value: app if value == "/app" else Path(value)):
            journal.Handler.do_GET(handler)
        self.assertEqual(outputs[-1][0], 200)
        self.assertEqual(outputs[-1][2], "text/javascript; charset=utf-8")
        self.assertIn(b"function render", outputs[-1][1])
        handler.path = "/assets/../journal.py"
        journal.Handler.do_GET(handler)
        self.assertEqual(outputs[-1][0], 404)


if __name__ == "__main__":
    unittest.main()
