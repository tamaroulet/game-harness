"""tests/test_scheduler.py の ResidentTests（12 件）。

    python -m unittest tests.test_scheduler_resident
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scheduler_split import pick  # noqa: E402
from test_scheduler import ResidentTests  # noqa: E402


def load_tests(loader, tests, pattern):
    return pick(loader, ResidentTests)
