"""部品の判定の受入試験（W-136、harness/arch_inventory.py）：判定の充足、コア到達可能性、削除モジュールの不在、凍結ベンチ・判定役の維持、評価文書の存在。

    python -m unittest tests.test_arch_verdicts -v
"""
import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import arch_inventory as ai  # noqa: E402
import progress  # noqa: E402

JUDGES = (
    "billiards_judge.py",
    "cartpole_judge.py",
    "catcher_judge.py",
    "domino_judge.py",
    "hillcar_judge.py",
    "launcher_judge.py",
    "maze_judge.py",
    "rollgame_judge.py",
    "springchain_judge.py",
    "test_testbed_judge_basic.py",
    "test_testbed_judge_stress.py",
    "tower_judge.py",
)


class TestArchVerdicts(unittest.TestCase):
    def test_condition_1_component_and_module_verdicts_and_tasks(self):
        """条件 1: 部品ごとの verdict/assumption/decide_when、module_verdicts、台帳タスクの存在と有効性。"""
        cfg = ai.load_config()
        comps = cfg.get("components", {})
        self.assertTrue(comps, "config/arch_components.json に components が無い")

        tasks = progress.load_with_archive().get("tasks", [])
        task_map = {t["id"]: t for t in tasks if isinstance(t, dict) and "id" in t}

        for c_name, c in comps.items():
            verdict = c.get("verdict")
            self.assertIn(verdict, ai.VERDICTS, f"部品 {c_name} の verdict '{verdict}' が不正（{ai.VERDICTS} のいずれか）")
            self.assertTrue(isinstance(c.get("assumption"), str) and c.get("assumption").strip(),
                            f"部品 {c_name} に assumption（補う弱さ）が空でない文字列で指定されていない")
            self.assertTrue(isinstance(c.get("decide_when"), str) and c.get("decide_when").strip(),
                            f"部品 {c_name} に decide_when（決める条件）が空でない文字列で指定されていない")

            if verdict in ("remove_later", "hypothesis"):
                task_id = c.get("decide_task")
                self.assertTrue(task_id, f"部品 {c_name}（verdict={verdict}）に decide_task が無い")
                self.assertIn(task_id, task_map, f"部品 {c_name} の decide_task '{task_id}' が台帳（過去ログ含む）に無い")
                self.assertNotEqual(task_map[task_id].get("status"), "withdrawn",
                                    f"部品 {c_name} の decide_task '{task_id}' は取り下げられている (withdrawn)")

            for mod, mv in c.get("module_verdicts", {}).items():
                m_verdict = mv.get("verdict")
                self.assertIn(m_verdict, ai.VERDICTS, f"部品 {c_name} のモジュール {mod} の verdict '{m_verdict}' が不正")
                self.assertTrue(isinstance(mv.get("why"), str) and mv.get("why").strip(),
                                f"部品 {c_name} のモジュール {mod} に why が空でない文字列で指定されていない")
                if m_verdict in ("remove_later", "hypothesis"):
                    m_task = mv.get("decide_task")
                    self.assertTrue(m_task, f"部品 {c_name} のモジュール {mod}（verdict={m_verdict}）に decide_task が無い")
                    self.assertIn(m_task, task_map, f"モジュール {mod} の decide_task '{m_task}' が台帳に無い")
                    self.assertNotEqual(task_map[m_task].get("status"), "withdrawn",
                                        f"モジュール {mod} の decide_task '{m_task}' は取り下げられている (withdrawn)")

    def test_condition_2_core_entrypoint_reachables_are_all_keep(self):
        """条件 2: entrypoints.core から辿れるモジュールはすべて判定が keep であること。"""
        cfg = ai.load_config()
        core_roots = cfg.get("entrypoints", {}).get("core", [])
        self.assertTrue(core_roots, "config/arch_components.json の entrypoints.core が空または未定義")

        reached = ai.reach(core_roots)
        comps = cfg.get("components", {})
        owner, unassigned, _ = ai.assign(comps, ai.modules())

        for mod in sorted(reached):
            self.assertIn(mod, owner, f"core から到達するモジュール {mod} がどの部品にも属していない")
            comp_name = owner[mod]
            verdict = ai.verdict_of(comps[comp_name], mod)
            self.assertEqual(verdict, "keep",
                             f"entrypoints.core から届くモジュール {mod}（部品: {comp_name}）の判定が keep ではない: {verdict}")

    def test_condition_3_remove_now_is_gone_and_removed_modules_not_imported(self):
        """条件 3: remove_now の不在、removed に記載されたモジュールの消去と import 撲滅。"""
        cfg = ai.load_config()
        comps = cfg.get("components", {})
        for c_name, c in comps.items():
            self.assertNotEqual(c.get("verdict"), "remove_now", f"部品 {c_name} に remove_now が残っている")
            for mod, mv in c.get("module_verdicts", {}).items():
                self.assertNotEqual(mv.get("verdict"), "remove_now", f"部品 {c_name} のモジュール {mod} に remove_now が残っている")

        removed = cfg.get("removed", {})
        self.assertIsInstance(removed, dict, "config/arch_components.json の removed が dict 形式ではない")

        removed_stems = set()
        removed_dotted = set()
        for r_name in removed:
            r_path = ROOT / "harness" / r_name
            self.assertFalse(r_path.exists(), f"removed に記録されたモジュール {r_name} が実在している")
            for c_name, c in comps.items():
                for pat in c.get("modules", []):
                    self.assertFalse(ai._matches(r_name, pat), f"removed の {r_name} が部品 {c_name} の modules に残っている")
                self.assertNotIn(r_name, c.get("module_verdicts", {}),
                                 f"removed の {r_name} が部品 {c_name} の module_verdicts に残っている")

            p = Path(r_name)
            removed_stems.add(p.stem)
            posix_no_ext = p.as_posix()
            if posix_no_ext.endswith(".py"):
                posix_no_ext = posix_no_ext[:-3]
            removed_dotted.add(posix_no_ext.replace("/", "."))

        target_mods = removed_stems | removed_dotted
        py_files = sorted(list((ROOT / "harness").rglob("*.py")) + list((ROOT / "tests").rglob("*.py")))

        for py_path in py_files:
            if "__pycache__" in py_path.parts:
                continue
            try:
                tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
            except SyntaxError:
                continue

            rel_file = py_path.relative_to(ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        name = alias.name
                        for t in target_mods:
                            bad = (name == t or name.startswith(t + ".") or
                                   name == f"harness.{t}" or name.startswith(f"harness.{t}."))
                            self.assertFalse(bad, f"{rel_file} が消去されたモジュール '{name}' を import している")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    for t in target_mods:
                        bad_mod = (mod == t or mod.startswith(t + ".") or
                                   mod == f"harness.{t}" or mod.startswith(f"harness.{t}."))
                        self.assertFalse(bad_mod, f"{rel_file} が消去されたモジュール '{mod}' から import している")
                        if mod in ("", "harness"):
                            for alias in node.names:
                                self.assertNotEqual(alias.name, t,
                                                    f"{rel_file} が {mod} から消去されたモジュール '{alias.name}' を import している")

    def test_condition_4_frozen_benchmarks_judges_and_factory_benchmarks_kept(self):
        """条件 4: 凍結 10 本・判定役・工廠ベンチが実在し、keep 判定されていること。"""
        for i in range(1, 11):
            bench_dir = ROOT / "docs" / "bench" / f"B{i:02d}"
            self.assertTrue(bench_dir.exists(), f"凍結ベンチ {bench_dir.relative_to(ROOT)} が存在しない")

        for judge_name in JUDGES:
            judge_path = ROOT / "tests" / judge_name
            self.assertTrue(judge_path.exists(), f"判定役テスト {judge_name} が tests/ に存在しない")

        cfg = ai.load_config()
        comps = cfg.get("components", {})
        self.assertIn("factory", comps, "config/arch_components.json に factory 部品が無い")
        factory_comp = comps["factory"]
        self.assertEqual(factory_comp.get("verdict"), "keep",
                         f"factory 部品の判定が keep ではない: {factory_comp.get('verdict')}")

        owner, _, _ = ai.assign(comps, ai.modules())
        factory_benches = [m for m, c in owner.items() if c == "factory" and Path(m).name.startswith("bench_")]
        self.assertTrue(factory_benches, "factory 部品に bench_*.py モジュールが見つからない")
        for fb in factory_benches:
            self.assertTrue((ROOT / "harness" / fb).exists(), f"工廠ベンチ {fb} が実在しない")
            v = ai.verdict_of(factory_comp, fb)
            self.assertEqual(v, "keep", f"工廠ベンチ {fb} の判定が keep ではない: {v}")

    def test_condition_5_evaluation_document_exists(self):
        """条件 5: docs/design/harness_evaluation.md が存在すること。"""
        doc_path = ROOT / "docs" / "design" / "harness_evaluation.md"
        self.assertTrue(doc_path.is_file(), f"判定理由の文書 {doc_path.relative_to(ROOT)} が存在しない")


class TestReach(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reach_traversal(self):
        """reach が a.py からの import/from/harness接頭辞/パッケージ相対を正しく辿り、孤立した d.py を除外すること。"""
        harness = self.tmp / "harness"
        harness.mkdir()
        (harness / "a.py").write_text("import b\nfrom p import x\nimport harness.e\n", encoding="utf-8")
        (harness / "b.py").write_text("from c import something\n", encoding="utf-8")
        (harness / "c.py").write_text("something = 1\n", encoding="utf-8")
        (harness / "d.py").write_text("# unused\n", encoding="utf-8")
        (harness / "e.py").write_text("# imported via harness.e\n", encoding="utf-8")

        pkg = harness / "p"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "x.py").write_text("def x(): pass\n", encoding="utf-8")

        got = ai.reach(["a.py"], root=self.tmp)
        expected = {"a.py", "b.py", "c.py", "e.py", "p/x.py"}
        self.assertEqual(got, expected, f"reach の到達結果が期待と一致しない: got={got}, expected={expected}")
        self.assertNotIn("d.py", got, "未使用モジュール d.py が reach の結果に含まれている")


if __name__ == "__main__":
    unittest.main()
