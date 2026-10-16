"""A/B 実験（docs/design/b4_ab_experiment.md）。driver・measure・report を持つ。

harness の他のモジュールは平らな import（`import project`）を使うので、ここで harness/ を
import の探索先に足してから読む。`python -m harness.ab.driver` でも `python harness/ab/driver.py` でも動く。
"""
import sys
from pathlib import Path

_HARNESS = str(Path(__file__).resolve().parent.parent)
if _HARNESS not in sys.path:
    sys.path.insert(0, _HARNESS)
