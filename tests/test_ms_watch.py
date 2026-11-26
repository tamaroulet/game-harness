"""WSL のディスクとメモリの安全網（harness/ms_watch.py）の部品の試験（docker に依らない）。

    python -m unittest tests.test_ms_watch -v
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import ms_watch  # noqa: E402


class WatchTests(unittest.TestCase):
    def test_docker_stats_sizes(self):
        self.assertEqual(ms_watch.to_mib("1.5GiB"), 1536)
        self.assertEqual(ms_watch.to_mib("512MiB "), 512)
        self.assertEqual(ms_watch.to_mib("2048KiB"), 2)
        self.assertIsNone(ms_watch.to_mib("--"))

    def test_stop_lines_are_disk_and_memory_only(self):
        self.assertEqual(ms_watch.stop_reasons(4096, 50 * 1024, 1.5, 10), [])
        self.assertEqual(len(ms_watch.stop_reasons(1000, 50 * 1024, 1.5, 10)), 1)
        self.assertIn("disk free", ms_watch.stop_reasons(4096, 9 * 1024, 1.5, 10)[0])
        self.assertEqual(len(ms_watch.stop_reasons(1000, 9 * 1024, 1.5, 10)), 2)
        self.assertEqual(ms_watch.stop_reasons(None, None, 1.5, 10), [], "読めない値では止めない")
        self.assertFalse(hasattr(ms_watch, "agy_quota"), "利用枠は見ない（操縦士の裁定 2026-11-26）")
        with self.assertRaises(SystemExit):
            ms_watch.main(["--box", "b", "--until", "u", "--log", "l", "--weekly-floor", "0.45"])

    def test_reads_meminfo_and_disk(self):
        with tempfile.TemporaryDirectory() as d:
            mi = Path(d) / "meminfo"
            mi.write_text("MemTotal:       22544000 kB\nMemAvailable:   10240000 kB\n", encoding="utf-8")
            self.assertEqual(ms_watch.mem_available_mib(str(mi)), 10000)
            self.assertIsNone(ms_watch.mem_available_mib(str(Path(d) / "none")))
            self.assertGreaterEqual(ms_watch.disk_free_mib(d), 0)


if __name__ == "__main__":
    unittest.main()
