"""tests/test_scheduler.py の SchedulerTests のうち、名前順の 34 番目から最後まで（17 件）。

    python -m unittest tests.test_scheduler_c
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduler_split import pick  # noqa: E402
from test_scheduler import SchedulerTests  # noqa: E402


def load_tests(loader, tests, pattern):
    return pick(loader, SchedulerTests, 34)
