"""B（ハーネス）の実装役は記憶を持たない（v3 §15 の 11-12 その 4：会話の形の B は外した）。前の段の分かることは Git の木と仕様と台帳から
受け取り、箱の作業の場所は課題のパッケージ・試験・仕様だけ、目次は harness/ ではなく課題のパッケージのもの。進め方は両腕に同じ課題の
手順の助言だけ。箱と H ラインは呼ばない（差し替える）。"""
import argparse
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_chain  # noqa: E402
import bench_run  # noqa: E402
import chain_compare  # noqa: E402
import hline_prompt  # noqa: E402
import symbolmap  # noqa: E402

INFO = {"title": "題", "kind": "足す", "reqs": [("REQ-01", "REQ-01：何かを足す")]}
PKG = '''"""課題のパッケージ。"""


def simulate(slope, steps=10):
    return [slope] * steps


class Car:
    def step(self, torque):
        return torque
'''


class Guidance(unittest.TestCase):
    def test_both_arms_get_the_same_task_advice_and_nothing_about_an_arm(self):
        what = bench_chain.what_text("B07", "pkg", 2, INFO, ["pkg/mod.py", "tests/test_pkg_s2.py"])
        head, _, tail = what.partition("## 進め方")
        self.assertIn("## 合格条件", head)
        self.assertIn("1 本のスクリプトにまとめて", tail)
        self.assertIn("git は使わなくてよい", tail)
        for arm_only in ("この会話", "読み直さなくてよい", "ほかの場所は読まなくてよい", "目次", "影響テスト", "次の試行"):
            self.assertNotIn(arm_only, what)


class Mark(unittest.TestCase):
    def test_the_box_mark_names_the_task_scope_and_no_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ns = argparse.Namespace(task="B07", run_id="t-s2", repo=tmp, base="HEAD", out=tmp, sessions=tmp, plan=None, implementer=None)
            st = bench_chain.ChainStage(ns, "S", Path(tmp) / "spec.md", "pkg", {})
            self.assertEqual(st.box_mark({"x": 1})["box"], {"package": "pkg", "docs": "docs/bench/B07/"})

    def test_there_is_no_chat_switch_for_b(self):
        with patch.object(bench_chain, "run", return_value=0) as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bench_chain.main(["run", "--tag", "t", "--repo", "r", "--base", "b", "--staged", "s", "--chat"]), 2)
        self.assertEqual(run.call_count, 0)

    def test_a_b_record_named_harness_chat_is_flagged(self):
        run = {"bases": {"B07": {}}, "set": "dev"}
        self.assertEqual(chain_compare.warnings_of([{**run, "arm": "chat"}], [{**run, "arm": None}]), [])
        self.assertEqual(len(chain_compare.warnings_of([{**run, "arm": "chat"}], [{**run, "arm": "harness-chat"}])), 1)


class Chain(unittest.TestCase):
    def test_every_stage_starts_a_new_conversation(self):
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged, out = root / "staged", root / "out"
            sha = {}
            for k in (1, 2):
                spec = staged / "B07" / f"s{k}" / "spec.md"
                spec.parent.mkdir(parents=True)
                spec.write_text(f"# 仕様 {k}\n", encoding="utf-8")
                sha[f"s{k}"] = hashlib.sha256(spec.read_bytes()).hexdigest()
            (staged / "chain.json").write_text(json.dumps({"box": True}), encoding="utf-8")
            entry = {"base": "B07", "package": "pkg", "stages": 2, "spec_sha256": sha}

            def run_stage(stage):
                seen.append(stage.box_mark({})["box"])
                (stage.rundir / "factory").mkdir(parents=True, exist_ok=True)
                (stage.rundir / "factory" / "run.json").write_text(json.dumps({"head": "H", "start": "S", "stop": "done", "tries": 1}),
                                                                   encoding="utf-8")
                return 0

            a = argparse.Namespace(tag="t", implementer=None, sessions=None, box_memo={})
            with patch.object(bench_chain, "s0_commit", return_value="S0"), patch.object(bench_chain, "fetch_tip", return_value="T"), \
                    patch.object(bench_chain, "spec_info", return_value=INFO), patch.object(bench_chain, "changeable", return_value=["pkg/m.py"]), \
                    patch.object(bench_chain, "start_commit", return_value="S"), \
                    patch.object(bench_chain, "write_plan", side_effect=lambda d, *a: d), \
                    patch.object(bench_chain, "run_stage", side_effect=run_stage):
                rec = bench_chain.run_chain(a, root, out, staged, "R", entry, "dev")
        self.assertEqual(seen, [{"package": "pkg", "docs": "docs/bench/B07/"}] * 2)
        self.assertNotIn("arm", rec)
        self.assertNotIn("conversation", rec)


class Context(unittest.TestCase):
    def tree(self, root):
        for rel, text in {"pkg/__init__.py": PKG, "tests/test_pkg_base.py": "import pkg\n", "tests/test_pkg_s1.py": "from pkg import Car\n",
                          "harness/box.py": '"""走らせ役。"""\ndef agent(cfg):\n    pass\n'}.items():
            (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(root) / rel).write_text(text, encoding="utf-8")

    def test_the_package_map_shows_the_task_and_not_the_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.tree(tmp)
            text = symbolmap.package_text(tmp, "pkg", "docs/bench/B07/", 30000)
        self.assertTrue(text.startswith(symbolmap.PKG_HEAD))
        self.assertIn("## pkg/__init__.py", text)
        self.assertIn("テスト: tests/test_pkg_base.py, tests/test_pkg_s1.py", text)
        self.assertIn("def simulate(slope, steps=10)", text)
        self.assertIn("class Car", text)
        self.assertIn("`docs/bench/B07/` だけを置いています", text)
        self.assertNotIn("harness/", text)

    def test_boxed_task_runs_get_the_package_map_and_others_the_harness_map(self):
        host = types.SimpleNamespace(symbolmap=types.SimpleNamespace(
            package_text=lambda wt, pkg, docs, size: f"PKG {pkg} {docs} {size}", prompt_text=lambda *a, **k: "HARNESS",
            limit=symbolmap.limit))
        cfg = {"symbol_map": {"max_chars": 300}}
        self.assertEqual(hline_prompt.context(host, {**cfg, "box": {"package": "pkg", "docs": "docs/bench/B07/"}}, "wt", "w"),
                         "PKG pkg docs/bench/B07/ 300")
        self.assertEqual(hline_prompt.context(host, {**cfg, "box": {"package": "pkg"}}, "wt", "w"), "HARNESS")
        self.assertEqual(hline_prompt.context(host, cfg, "wt", "w"), "HARNESS")

    def test_a_broken_package_does_not_stop_the_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "pkg").mkdir()
            (Path(tmp) / "pkg" / "__init__.py").write_text("def broken(:\n", encoding="utf-8")
            text = symbolmap.package_text(tmp, "pkg", "docs/bench/B07/", 30000)
        self.assertIn("飛ばした（構文の誤り・読めない）: pkg/__init__.py", text)
        self.assertIn("試験（まだ無い）", text)


if __name__ == "__main__":
    unittest.main()
