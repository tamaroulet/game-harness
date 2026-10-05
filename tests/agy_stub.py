"""agy の導入先の照合（pipeline.guard_implementer）を、テストの間だけ無効にする助け手。test_ で始めないので全件の走行の対象外。"""
import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

import pipeline  # noqa: E402


@contextlib.contextmanager
def disabled():
    original = pipeline.guard_implementer
    pipeline.guard_implementer = lambda *args, **kwargs: None
    try:
        yield
    finally:
        pipeline.guard_implementer = original


def install(case):
    cm = disabled()
    cm.__enter__()
    case.addCleanup(cm.__exit__, None, None, None)
