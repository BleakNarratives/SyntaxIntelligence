"""
Test suite for crash_log.py — always-on JSONL step logger.

Run from `bleaknarratives/Syntax-Intensity/`:
    python3 -m unittest test_crash_log -v

Or as part of the suite:
    python3 -m unittest discover -s bleaknarratives/Syntax-Intelligence -p 'test_*.py'
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import crash_log


class TestCrashLogWrite(unittest.TestCase):

    def setUp(self):
        os.environ["CRASH_LOG_SKIP_LOAD_MARKER"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="crash_log_test_"))
        self.log = self.tmp / "log.jsonl"
        crash_log.set_path(self.log)
        crash_log.fsync_each(False)  # speed up test
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        # Reset module-level singletons so the post-test cleanup is clean
        crash_log._SESSION_REGISTRY.clear()

    def tearDown(self):
        os.environ.pop("CRASH_LOG_SKIP_LOAD_MARKER", None)
        crash_log.set_path(None)
        crash_log.fsync_each(True)

    def test_step_writes_one_row(self):
        crash_log.step("hello", note="first")
        rows = crash_log.read_all()
        # Filter out the module-load marker row that fires on import.
        ours = [r for r in rows if r.get("event") == "hello"]
        self.assertEqual(len(ours), 1)
        self.assertEqual(ours[0]["event"], "hello")
        self.assertEqual(ours[0]["note"], "first")
        self.assertEqual(ours[0]["pid"], os.getpid())

    def test_step_includes_base_metadata(self):
        crash_log.step("meta")
        rows = [r for r in crash_log.read_all() if r.get("event") == "meta"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ("ts", "pid", "host", "module", "event"):
            self.assertIn(key, row)
        self.assertEqual(row["module"], "crash_log")
        self.assertIsInstance(row["ts"], float)

    def test_multiple_steps_persist(self):
        for i in range(5):
            crash_log.step("tick", n=i)
        rows = [r for r in crash_log.read_all() if r.get("event") == "tick"]
        self.assertEqual(len(rows), 5)
        counter = sorted(r["n"] for r in rows)
        self.assertEqual(counter, [0, 1, 2, 3, 4])


# ════════════════════════════════════════════════════════════════
# SESSION LIFECYCLE
# ════════════════════════════════════════════════════════════════

class TestSession(unittest.TestCase):

    def setUp(self):
        os.environ["CRASH_LOG_SKIP_LOAD_MARKER"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="crash_log_session_test_"))
        self.log = self.tmp / "log.jsonl"
        crash_log.set_path(self.log)
        crash_log.fsync_each(False)
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        crash_log._SESSION_REGISTRY.clear()

    def tearDown(self):
        os.environ.pop("CRASH_LOG_SKIP_LOAD_MARKER", None)
        crash_log.set_path(None)
        crash_log.fsync_each(True)

    def test_open_then_close_persists_two_rows(self):
        sid = crash_log.open_session("smoke", seed=42)
        crash_log.close_session(sid, status="ok", note="done")

        rows = crash_log.read_all()
        types = [(r.get("event"), r.get("sid")) for r in rows
                 if r.get("event") in ("session_open", "session_close")]
        events = [t for t in types]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][0], "session_open")
        self.assertEqual(events[1][0], "session_close")
        self.assertEqual(events[0][1], sid)
        self.assertEqual(events[1][1], sid)

    def test_close_session_records_duration(self):
        sid = crash_log.open_session("duration-test")
        time.sleep(0.05)
        crash_log.close_session(sid, status="ok")
        rows = [r for r in crash_log.read_all() if r.get("event") == "session_close"]
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0].get("duration_seconds", 0), 0.04)

    def test_session_id_is_hex(self):
        import re
        sid = crash_log.open_session("hex-check")
        crash_log.close_session(sid, status="ok")
        self.assertRegex(sid, r"^[0-9a-f]{32}$")


# ════════════════════════════════════════════════════════════════
# THREAD SAFETY
# ════════════════════════════════════════════════════════════════

class TestThreadSafe(unittest.TestCase):

    def setUp(self):
        os.environ["CRASH_LOG_SKIP_LOAD_MARKER"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="crash_log_threads_"))
        self.log = self.tmp / "log.jsonl"
        crash_log.set_path(self.log)
        crash_log.fsync_each(False)
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)
        crash_log._SESSION_REGISTRY.clear()

    def tearDown(self):
        os.environ.pop("CRASH_LOG_SKIP_LOAD_MARKER", None)
        crash_log.set_path(None)
        crash_log.fsync_each(True)

    def test_concurrent_writers_no_loss(self):
        per_thread = 20
        n_threads = 5
        threads = []

        def worker(tid: int):
            for i in range(per_thread):
                crash_log.step("threads.tick", tid=tid, i=i)

        for t in range(n_threads):
            th = threading.Thread(target=worker, args=(t,))
            threads.append(th)
            th.start()
        for th in threads:
            th.join()

        rows = [r for r in crash_log.read_all() if r.get("event") == "threads.tick"]
        self.assertEqual(len(rows), n_threads * per_thread)


# ════════════════════════════════════════════════════════════════
# REPLAY — tolerate truncation
# ════════════════════════════════════════════════════════════════

class TestReplayRobustness(unittest.TestCase):

    def setUp(self):
        os.environ["CRASH_LOG_SKIP_LOAD_MARKER"] = "1"
        self.tmp = Path(tempfile.mkdtemp(prefix="crash_log_replay_test_"))
        self.log = self.tmp / "log.jsonl"
        crash_log.set_path(self.log)
        crash_log.fsync_each(False)
        self.addCleanup(shutil.rmtree, str(self.tmp), ignore_errors=True)

    def tearDown(self):
        os.environ.pop("CRASH_LOG_SKIP_LOAD_MARKER", None)
        crash_log.set_path(None)
        crash_log.fsync_each(True)

    def test_truncated_tail_does_not_crash(self):
        # Write a couple clean rows, then truncate mid-line (simulate
        # a process that died with a partial write).
        crash_log.step("clean1")
        crash_log.step("clean2")
        with open(self.log, "a", encoding="utf-8") as f:
            f.write('{"ts": 1.0, "pid": 999, "event": "PART')  # truncated
        rows = crash_log.read_all()
        # We get the two clean rows, and the truncated tail was treated
        # as truncated (read_all stops at the JSONDecodeError boundary).
        events = [r.get("event") for r in rows]
        self.assertIn("clean1", events)
        self.assertIn("clean2", events)
        # PART truncated row should NOT appear in parsed rows.
        self.assertNotIn("PART", events)


# ════════════════════════════════════════════════════════════════
# PATH RESOLUTION
# ════════════════════════════════════════════════════════════════

class TestPathResolution(unittest.TestCase):

    def test_default_path_returns_a_path(self):
        crash_log.set_path(None)
        p = crash_log.path()
        self.assertIsInstance(p, Path)
        self.assertTrue(str(p).endswith("crash_log.jsonl"))

    def test_set_path_round_trip(self):
        override = Path("/tmp/some_custom_path.jsonl")
        crash_log.set_path(override)
        try:
            self.assertEqual(crash_log.path(), override)
        finally:
            crash_log.set_path(None)


# ════════════════════════════════════════════════════════════════
# EASTER
# ════════════════════════════════════════════════════════════════

class TestEaster(unittest.TestCase):
    def test_easter_constants(self):
        self.assertEqual(crash_log.EASTER_BLOOM, "✶")
        self.assertIn("disk", crash_log.SAYING.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
