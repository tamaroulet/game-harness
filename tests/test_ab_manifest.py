"""A/B 実験の凍結した入力（experiments/b4_ab、docs/design/b4_ab_experiment.md §4、B4-E3）。

    python -m unittest discover -s tests -v

**なぜ要るか**: 実験の公平性は「両条件に同じ入力を与える」ことに懸かっている。要求文・単位定義・生成テスト・
テンプレートが 1 バイトでも変われば、走行の間で入力が揺らぐ。マニフェストの sha256 と実物が一致すること、
5 タスクの interface が互いに矛盾しないことを確かめる。
"""
import hashlib
import json
import string
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXP = ROOT / "experiments" / "b4_ab"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Manifest(unittest.TestCase):
    def setUp(self):
        self.m = json.loads((EXP / "tasks.json").read_text(encoding="utf-8"))

    def test_five_tasks_in_fixed_order(self):
        self.assertEqual([t["id"] for t in self.m["tasks"]], ["T1", "T2", "T3", "T4", "T5"])
        self.assertEqual(self.m["max_attempts"], 3)

    def test_every_frozen_file_matches_its_hash(self):
        m = self.m
        self.assertEqual(sha(EXP / m["common_requirement"]), m["common_requirement_sha256"])
        for key in ("initial", "retry"):
            self.assertEqual(sha(EXP / m["templates"][key]), m["templates"][f"{key}_sha256"], key)
        for t in m["tasks"]:
            with self.subTest(t["id"]):
                self.assertEqual(sha(EXP / t["requirement"]), t["requirement_sha256"])
                self.assertEqual(sha(EXP / t["unit"]), t["unit_sha256"])
                files = sorted(p.name for p in (EXP / t["tests"]).iterdir())
                self.assertEqual(files, sorted(t["tests_sha256"]))
                for name, digest in t["tests_sha256"].items():
                    self.assertEqual(sha(EXP / t["tests"] / name), digest, name)

    def test_templates_have_only_known_placeholders(self):
        allowed = {"initial": {"task_id", "title", "workdir", "prompt", "interface", "whitelist", "test_dir"},
                   "retry": {"task_id", "attempt", "max_attempts", "failed_tests", "tail_lines", "failure_tail"}}
        for key, names in allowed.items():
            text = (EXP / self.m["templates"][key]).read_text(encoding="utf-8")
            used = {f for _, f, _, _ in string.Formatter().parse(text) if f}
            self.assertEqual(used, names, key)

    def test_units_are_v2_features_with_a_consistent_interface(self):
        seen = {}
        for t in self.m["tasks"]:
            unit = json.loads((EXP / t["unit"]).read_text(encoding="utf-8"))
            self.assertEqual(unit["schema"], 2)
            self.assertEqual(unit.get("task_kind"), "feature", t["id"])
            for ty in unit["interface"]["types"]:
                if ty["kind"] == "enum":
                    key, shape = (ty["name"],), tuple(ty["values"])
                    self.assertEqual(seen.setdefault(key, shape), shape, (t["id"], key))
                    continue
                for mem in ty.get("members", []):
                    key = (ty["name"], mem["name"], mem["kind"], tuple(p["name"] for p in mem.get("params", [])))
                    shape = (mem.get("type"), tuple(p["type"] for p in mem.get("params", [])), bool(mem.get("static")))
                    self.assertEqual(seen.setdefault(key, shape), shape, (t["id"], key))
        # 全タスク共通の入力の形（requirements/common.md）
        self.assertEqual(seen[("TickInput", "TickInput", "ctor", ("left", "right", "rotateCw", "rotateCcw",
                                                                 "softDrop", "hardDrop"))][1], ("bool",) * 6)


if __name__ == "__main__":
    unittest.main()
