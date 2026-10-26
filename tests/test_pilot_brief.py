"""高リスク（Tier 2）の PR に付ける 5 行の要約のオラクル試験（from: director、総監督 54 本目）。

1. 1 つの命令（受入 1）:
   - python -m harness.pilot_brief <PR 番号> で走る。main(argv, cfg=...) を呼べる形。
   - 引数がちょうど 1 つでなければ 1 で終わり、何も出さない。
   - 出力はちょうど 5 行。行頭の札がこの順で並ぶ:
     1. 要旨と理由:
     2. 高リスクの理由:
     3. 検証テストの書き手:
     4. 副作用と配線の到達:
     5. レビュアーの批評:
   - 改行の入った中身でも 5 行が崩れない。
2. 中身は機械で作る（受入 2）:
   - 要旨と理由: 枝の写しの .branch.json の枝名から台帳タスクの title と purpose。
   - 高リスクの理由: 枝写しと main 写しの変更差分を層の表に照合。当たった区分とパス。既存 tests/ の変更削除。
   - 検証テストの書き手: 台帳タスクの oracle の path と from。
   - 副作用と配線の到達: harness/ と tests/ の外のパス、触れた harness/ の部品、配線の到達は未達と固定。
   - レビュアーの批評: Argon 記録（kind: post, target: PR番号）の最後の decision と rationale。HIGHなら頭に HIGH。
   - 出どころが無いとき: 無し（<出どころの名前>）
   - 手で書いた数が無い: パス数等の可変性、決め打ち数値なし。
   - 低リスク PR: 層の表に当たるパスが無ければ「低リスク：5 行の要約は要らない」の 1 行で 0 で終了。
3. 報告の節と消した決まり（受入 3）:
   - CLAUDE.md の報告の節にこの命令の名前があること。
   - 消した決まり（PR-33, IX-22, IX-23）が CLAUDE.md, docs/director/**, docs/prompts/** に無いこと。
4. 操縦士の承認（受入 4）:
   - docs/design/decision_log.md に W-154、操縦士、承認の行があること。
"""
import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from harness import pilot_brief
except ImportError:
    pilot_brief = None

EXPECTED_HEADERS = [
    "要旨と理由:",
    "高リスクの理由:",
    "検証テストの書き手:",
    "副作用と配線の到達:",
    "レビュアーの批評:",
]

REMOVED_RULES = [
    ("PR-33", "次の節をこの順で、省かずに出す"),
    ("IX-22", "操縦士に上げるのは、L5 のこと"),
    ("IX-23", "現在地や状態を作文で書き直さない"),
]


def make_test_env(base_dir: Path, pr_num: str = "311", is_high_risk: bool = True):
    """仮の一時ディレクトリ環境を作成する（42 本目の教え: 本物の置き場には触れない）。"""
    mirror_dir = base_dir / "mirror"
    branches_dir = base_dir / "branches"
    branch_dir = branches_dir / pr_num
    docs_dir = base_dir / "docs"
    config_dir = base_dir / "config"
    argon_dir = base_dir / "argon"

    mirror_dir.mkdir(parents=True, exist_ok=True)
    branch_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "design").mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    argon_dir.mkdir(parents=True, exist_ok=True)

    # 1. main の写し
    (mirror_dir / ".mirror.json").write_text(json.dumps({"sha": "main_sha_001"}), encoding="utf-8")
    (mirror_dir / "tests").mkdir(exist_ok=True)
    (mirror_dir / "tests" / "test_existing.py").write_text("# existing test", encoding="utf-8")
    (mirror_dir / "harness").mkdir(exist_ok=True)
    (mirror_dir / "harness" / "existing.py").write_text("# existing harness", encoding="utf-8")

    # 2. 枝の写し
    branch_info = {
        "sha": "branch_sha_002",
        "branch": "task/W-154-pilot-report",
    }
    (branch_dir / ".branch.json").write_text(json.dumps(branch_info), encoding="utf-8")

    if is_high_risk:
        # 高リスク対象パスの変更（例: 既存テストの変更 + 外側パス + harness 部品）
        (branch_dir / "tests").mkdir(exist_ok=True)
        (branch_dir / "tests" / "test_existing.py").write_text("# modified existing test", encoding="utf-8")
        (branch_dir / "config").mkdir(exist_ok=True)
        (branch_dir / "config" / "sample.json").write_text("{}", encoding="utf-8")
        (branch_dir / "harness").mkdir(exist_ok=True)
        (branch_dir / "harness" / "director_launch.py").write_text("# touched harness module", encoding="utf-8")
    else:
        # 低リスク（ドキュメントのみの変更など、層の表に当たらない）
        (branch_dir / "docs").mkdir(exist_ok=True)
        (branch_dir / "docs" / "note.md").write_text("# just a note", encoding="utf-8")

    # 3. 台帳 progress.yaml
    progress_yaml = (
        "active_task_id: W-154\n"
        "tasks:\n"
        "- id: W-154\n"
        "  title: 高リスク（Tier 2）の PR に付ける 5 行の要約の形と道を作る\n"
        "  purpose: 操縦士が短く判断できるように機械で要約を作る\n"
        "  oracle:\n"
        "  - path: tests/test_pilot_brief.py\n"
        "    from: director\n"
    )
    (docs_dir / "progress.yaml").write_text(progress_yaml, encoding="utf-8")

    # 4. 層の表 config/risk_tiers.json
    risk_tiers = {
        "tiers": {
            "tier2": {
                "ci_workflow": [".github/workflows/*.yml"],
                "judgement_script": ["knowledge/scripts/diff_quarantine.py"],
                "resident_worker": ["harness/hline_cron.py"],
                "permission": ["config/permissions.json"],
                "existing_test": ["tests/**"],
            }
        }
    }
    (config_dir / "risk_tiers.json").write_text(json.dumps(risk_tiers, indent=2), encoding="utf-8")

    # 5. 部品表 config/arch_components.json
    arch_components = {
        "components": {
            "director": {
                "title": "総監督",
                "modules": ["director_launch.py"],
            }
        }
    }
    (config_dir / "arch_components.json").write_text(json.dumps(arch_components, indent=2), encoding="utf-8")

    # 6. Argon 評価記録 evals.jsonl
    argon_record = {
        "kind": "post",
        "target": pr_num,
        "decision": "HIGH",
        "rationale": "変更規模が大きく既存テストへの影響があるため要警戒",
    }
    (argon_dir / "evals.jsonl").write_text(json.dumps(argon_record) + "\n", encoding="utf-8")

    return {
        "root": base_dir,
        "mirror_dir": mirror_dir,
        "branches_dir": branches_dir,
        "progress_path": docs_dir / "progress.yaml",
        "decision_log_path": docs_dir / "design" / "decision_log.md",
        "risk_tiers_path": config_dir / "risk_tiers.json",
        "arch_components_path": config_dir / "arch_components.json",
        "argon_dir": argon_dir,
    }


class TestPilotBriefInvocation(unittest.TestCase):
    """受入 1: 1 つの命令で 5 行の要約を出力する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_test_"))
        self.cfg = make_test_env(self.tmp, pr_num="311", is_high_risk=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_brief(self, argv, cfg=None):
        if pilot_brief is None:
            self.fail("harness.pilot_brief がまだ実装されていません")
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pilot_brief.main(argv, cfg=cfg or self.cfg)
        return code, out.getvalue(), err.getvalue()

    def test_requires_exactly_one_argument(self):
        """引数がちょうど 1 つでなければ 1 で終わり、何も出さない。"""
        # 引数 0 個
        code, out, _ = self.run_brief([])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

        # 引数 2 個
        code, out, _ = self.run_brief(["311", "extra"])
        self.assertEqual(code, 1)
        self.assertEqual(out, "")

    def test_high_risk_produces_exact_five_lines_with_expected_headers(self):
        """高リスク PR では出力がちょうど 5 行で、行頭の札が規定順に並ぶ。"""
        code, out, _ = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 5, f"出力はちょうど 5 行でなければならない: {out}")
        for i, expected_header in enumerate(EXPECTED_HEADERS):
            self.assertTrue(
                lines[i].startswith(expected_header),
                f"行 {i+1} は '{expected_header}' で始まる必要がある。実測: '{lines[i]}'",
            )

    def test_multiline_content_does_not_break_five_lines(self):
        """改行の入った中身（purpose 等）を渡しても 5 行が崩れない。"""
        # purpose に改行を複数含む台帳を作成
        multiline_progress = (
            "active_task_id: W-154\n"
            "tasks:\n"
            "- id: W-154\n"
            "  title: 高リスク要約\n"
            "  purpose: 1 行目の目的。\n    2 行目の目的。\n    3 行目の目的。\n"
            "  oracle:\n"
            "  - path: tests/test_pilot_brief.py\n"
            "    from: director\n"
        )
        self.cfg["progress_path"].write_text(multiline_progress, encoding="utf-8")

        code, out, _ = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 5, f"改行入りでもちょうど 5 行でなければならない: {out}")


class TestPilotBriefMechanicalContent(unittest.TestCase):
    """受入 2: 中身は機械で作られ、仮の出どころの値を変えると出力の行が変わる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_test_"))
        self.cfg = make_test_env(self.tmp, pr_num="311", is_high_risk=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_brief(self, argv, cfg=None):
        if pilot_brief is None:
            self.fail("harness.pilot_brief がまだ実装されていません")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = pilot_brief.main(argv, cfg=cfg or self.cfg)
        return code, out.getvalue()

    def test_content_changes_when_sources_change(self):
        """仮の出どころの値を変えると、出力の各行が変わる（機械的生成の証明）。"""
        _, out1 = self.run_brief(["311"])
        lines1 = out1.splitlines()

        # 出どころ 1: 台帳の title / purpose を変更
        mod_progress = (
            "active_task_id: W-154\n"
            "tasks:\n"
            "- id: W-154\n"
            "  title: まったく新しいタイトルABC\n"
            "  purpose: まったく新しい目的XYZ\n"
            "  oracle:\n"
            "  - path: tests/test_new_oracle.py\n"
            "    from: someone_else\n"
        )
        self.cfg["progress_path"].write_text(mod_progress, encoding="utf-8")

        # 出どころ 2: Argon 評価記録の rationale を変更
        mod_argon = {
            "kind": "post",
            "target": "311",
            "decision": "HIGH",
            "rationale": "まったく異なる批評テキスト999",
        }
        (self.cfg["argon_dir"] / "evals.jsonl").write_text(json.dumps(mod_argon) + "\n", encoding="utf-8")

        _, out2 = self.run_brief(["311"])
        lines2 = out2.splitlines()

        # 1. 要旨と理由が変化している
        self.assertNotEqual(lines1[0], lines2[0])
        self.assertIn("まったく新しいタイトルABC", lines2[0])
        self.assertIn("まったく新しい目的XYZ", lines2[0])

        # 3. 検証テストの書き手が変化している
        self.assertNotEqual(lines1[2], lines2[2])
        self.assertIn("tests/test_new_oracle.py", lines2[2])
        self.assertIn("someone_else", lines2[2])

        # 5. レビュアーの批評が変化している
        self.assertNotEqual(lines1[4], lines2[4])
        self.assertIn("まったく異なる批評テキスト999", lines2[4])

    def test_low_risk_pr_produces_single_line_message(self):
        """低リスクの PR（層の表に当たるパスが無い PR）は 1 行を出して 0 で終わる。"""
        low_tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_low_"))
        try:
            cfg_low = make_test_env(low_tmp, pr_num="312", is_high_risk=False)
            code, out = self.run_brief(["312"], cfg=cfg_low)
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0], "低リスク：5 行の要約は要らない")
        finally:
            shutil.rmtree(low_tmp, ignore_errors=True)

    def test_high_risk_reasons_and_existing_tests(self):
        """高リスクの理由は、当たった区分とパスを書き、既存 tests/ 変更を検知する。"""
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        high_risk_line = lines[1]
        self.assertIn("高リスクの理由:", high_risk_line)
        # tests/test_existing.py が既存テスト変更として記載されていること
        self.assertIn("tests/test_existing.py", high_risk_line)

    def test_side_effects_and_wiring_reach(self):
        """副作用と配線の到達: 外側パス、harness 部品、未達固定文。"""
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        side_line = lines[3]
        # 外側パス (config/sample.json)
        self.assertIn("config/sample.json", side_line)
        # 部品 (総監督 / director)
        self.assertTrue("総監督" in side_line or "director" in side_line)
        # 配線到達は未達固定
        self.assertIn("配線の検査は未達（req.wiring-executed）", side_line)

    def test_argon_high_shows_high_prefix(self):
        """Argon の判定が HIGH なら、批評の行の頭に HIGH が見える。"""
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        critique_line = lines[4]
        self.assertTrue(critique_line.startswith("レビュアーの批評: HIGH") or "HIGH" in critique_line)

    def test_missing_sources_handled_gracefully(self):
        """出どころが無いときは行に「無し（<出どころの名前>）」と書き、値を作らない。"""
        # 1. Argon の記録が無い
        (self.cfg["argon_dir"] / "evals.jsonl").unlink()
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("無し（Argon", lines[4])

        # 2. oracle が無い
        no_oracle_progress = (
            "active_task_id: W-154\n"
            "tasks:\n"
            "- id: W-154\n"
            "  title: 高リスク要約\n"
            "  purpose: 目的\n"
        )
        self.cfg["progress_path"].write_text(no_oracle_progress, encoding="utf-8")
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("無し（oracle）", lines[2])

        # 3. 枝名からタスクが引けない
        unmatched_branch = {
            "sha": "branch_sha_999",
            "branch": "task/W-999-unknown-task",
        }
        (self.cfg["branches_dir"] / "311" / ".branch.json").write_text(
            json.dumps(unmatched_branch), encoding="utf-8"
        )
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("無し（台帳タスク）", lines[0])

    def test_no_hardcoded_numbers(self):
        """出力に出る数（パス数等）は仮の写しを変えると変わり、コードに決め打ちがない。"""
        # パスをさらに 2 つ追加
        branch_dir = self.cfg["branches_dir"] / "311"
        (branch_dir / "config" / "extra1.json").write_text("{}", encoding="utf-8")
        (branch_dir / "config" / "extra2.json").write_text("{}", encoding="utf-8")

        _, out_more = self.run_brief(["311"])
        # パスが増えたことで出力が変化する
        self.assertIn("extra1.json", out_more)
        self.assertIn("extra2.json", out_more)


class TestPilotBriefClaudeMdAndRules(unittest.TestCase):
    """受入 3: game-harness の CLAUDE.md の報告の節と、消した決まりの非存在。"""

    def test_claude_md_report_section_has_command_name(self):
        """CLAUDE.md の報告の節にこの命令（harness.pilot_brief）の名前があること。"""
        claude_md_path = ROOT / "CLAUDE.md"
        self.assertTrue(claude_md_path.exists(), "CLAUDE.md が存在すること")
        content = claude_md_path.read_text(encoding="utf-8")
        self.assertIn("harness.pilot_brief", content, "CLAUDE.md に harness.pilot_brief の記載があること")

    def test_removed_rules_do_not_exist_in_docs(self):
        """消した決まり（PR-33, IX-22, IX-23）が CLAUDE.md・docs/director/**・docs/prompts/** に無いこと。"""
        check_paths = [ROOT / "CLAUDE.md"]
        for p in (ROOT / "docs" / "director").glob("**/*"):
            if p.is_file():
                check_paths.append(p)
        for p in (ROOT / "docs" / "prompts").glob("**/*"):
            if p.is_file():
                check_paths.append(p)

        # rules_inventory.md 自身は除く
        check_paths = [p for p in check_paths if p.name != "rules_inventory.md"]

        for path in check_paths:
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                continue
            for rule_id, phrase in REMOVED_RULES:
                self.assertNotIn(
                    phrase,
                    text,
                    f"消した決まり {rule_id} の文言言及が {path} に残っています: '{phrase}'",
                )


class TestPilotBriefDecisionLog(unittest.TestCase):
    """受入 4: 操縦士が出力の形を承認した行が docs/design/decision_log.md にある。"""

    def test_pilot_approval_row_exists_in_decision_log(self):
        """decision_log.md にタスク W-154、操縦士、承認の行があること。"""
        log_path = ROOT / "docs" / "design" / "decision_log.md"
        self.assertTrue(log_path.exists(), "decision_log.md が存在すること")
        text = log_path.read_text(encoding="utf-8")
        rows = [l for l in text.splitlines() if re.match(r"^\| 20\d\d-\d\d-\d\d \|", l)]

        # W-154 と 操縦士 と 承認 を含む行を探す
        found = [r for r in rows if "W-154" in r and "操縦士" in r and "承認" in r]
        self.assertTrue(
            found,
            "docs/design/decision_log.md に W-154、操縦士、承認を含む行が見つかりません",
        )


if __name__ == "__main__":
    unittest.main()
