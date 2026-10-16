"""tests/test_scheduler_*.py が、tests/test_scheduler.py の試験クラスから走らせる試験を選ぶ。

test_ で始まらない名前なので、探索（discover）には拾われない。
"""
import unittest


def pick(loader, case, start=0, stop=None):
    """case の試験を名前順に並べ、start から stop の手前までを組にして返す。"""
    names = sorted(loader.getTestCaseNames(case))[start:stop]
    return unittest.TestSuite(case(name) for name in names)
