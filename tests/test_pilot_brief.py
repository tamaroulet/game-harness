"""高リスク（Tier 2）の PR に付ける 5 行の要約のオラクル試験（from: director、総監督 54・55 本目）。

1. 1 つの命令（受入 1）:
   - python -m harness.pilot_brief <PR 番号> で走る。main(argv, cfg=None, root=ROOT) を呼べる形。
   - 引数がちょうど 1 つでなければ 1 で終わり、何も出さない。
   - 出力はちょうど 5 行。行頭の札がこの順で並ぶ:
     1. 要旨と理由:
     2. 高リスクの理由:
     3. 検証テストの書き手:
     4. 副作用と配線の到達:
     5. レビュアーの批評:
   - 改行の入った中身でも 5 行が崩れない。
2. 中身は機械で作る（受入 2）:
   - 枝写しは main 写しの全部をコピーした上で変更を当てる（差分だけではない、55 本目 §3-1）。
   - 既存テスト判定: main 写しにある tests/ の変更・削除のみ高リスク。新規テスト追加は低リスク（55 本目 §3-2）。
   - 高リスクの理由: 当たった区分名とパスの両方を出す。既存テストの変更・削除、CI ワークフロー等の複数区分（55 本目 §3-3）。
   - 本物の層の表: config/risk_tiers.json を読め、5 区分がすべてあり、空でない（55 本目 §3-3）。
   - 副作用と配線の到達: harness/ と tests/ の外のパス、触れた harness/ の部品名、配線到達は「配線の検査は未達（req.wiring-executed）」固定。
   - レビュアーの批評: Argon 記録の post で対象 PR の最後の 1 件。行頭に HIGH / LOW が見える（55 本目 §3-4）。
   - 出どころが無いとき: 「無し（<出どころの名前>）」。枝名からタスクが引けないときは要旨と理由だけでなく検証テストの書き手も無し（台帳タスク）になる（55 本目 §4）。
   - 本物の設定構造: cfg は mirror, branches, argon を持ち、root でリポジトリ根を渡す（55 本目 §3-5）。
   - 低リスク PR: 層の表に当たらなければ「低リスク：5 行の要約は要らない」の 1 行で 0 で終了。
3. 報告の節と消した決まり（受入 3）:
   - CLAUDE.md の報告の節にこの命令（harness.pilot_brief）の名前があること（55 本目 §4）。
   - 消した決まり（PR-33, IX-22, IX-23）が CLAUDE.md, docs/director/**, docs/prompts/** に無いこと。
4. 操縦士の承認（受入 4）:
   - docs/design/decision_log.md に W-154、操縦士、承認の行があること。
"""
import contextlib
import fnmatch
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
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

TIER2_CATEGORIES = [
    "CI のワークフロー",
    "合否を決めるスクリプト",
    "常駐ワーカー",
    "権限",
    "既存のテスト",
]


def make_test_env(base_dir: Path, pr_num: str = "311", is_high_risk: bool = True):
    """仮の一時ディレクトリ環境を作成する（42 本目の教え: 本物の置き場には触れない）。
    55 本目 §3-1: 枝の写しは main の写しの全部をコピーしてから変更を当てる。
    55 本目 §3-5: cfg は本物（config/hline.json）と同じ mirror, branches, argon を持ち、
    台帳・層の表・部品表等は root 配下に置く。
    """
    root_dir = base_dir / "root"
    mirror_dir = base_dir / "mirror"
    branches_dir = base_dir / "branches"
    branch_dir = branches_dir / pr_num
    argon_dir = base_dir / "out" / "hline" / "argon"

    root_dir.mkdir(parents=True, exist_ok=True)
    mirror_dir.mkdir(parents=True, exist_ok=True)
    branches_dir.mkdir(parents=True, exist_ok=True)
    argon_dir.mkdir(parents=True, exist_ok=True)

    docs_dir = root_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "design").mkdir(parents=True, exist_ok=True)
    config_dir = root_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # 1. main の写し（全部のファイル群）
    (mirror_dir / ".mirror.json").write_text(json.dumps({"sha": "main_sha_001"}), encoding="utf-8")
    (mirror_dir / "tests").mkdir(exist_ok=True)
    (mirror_dir / "tests" / "test_existing.py").write_text("# existing test in main", encoding="utf-8")
    (mirror_dir / "harness").mkdir(exist_ok=True)
    (mirror_dir / "harness" / "existing.py").write_text("# existing harness in main", encoding="utf-8")

    # 2. 枝の写し: 55 本目 §3-1 に従い、まず main の写しを丸ごとコピーする
    shutil.copytree(mirror_dir, branch_dir, dirs_exist_ok=True)

    # 枝のメタデータ
    branch_info = {
        "sha": "branch_sha_002",
        "branch": "task/W-154-pilot-report",
    }
    (branch_dir / ".branch.json").write_text(json.dumps(branch_info), encoding="utf-8")

    if is_high_risk:
        # 高リスク対象パスの変更: 既存テストの変更 + 外側パス追加 + harness 部品追加
        (branch_dir / "tests" / "test_existing.py").write_text("# modified existing test", encoding="utf-8")
        (branch_dir / "config").mkdir(exist_ok=True)
        (branch_dir / "config" / "sample.json").write_text("{}", encoding="utf-8")
        (branch_dir / "harness" / "director_launch.py").write_text("# touched director module", encoding="utf-8")
    else:
        # 低リスク: ドキュメントのみ追加（main のファイルはそのまま残る）
        (branch_dir / "docs").mkdir(exist_ok=True)
        (branch_dir / "docs" / "note.md").write_text("# just a note", encoding="utf-8")

    # 3. 台帳 progress.yaml (root 配下)
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

    # 4. 層の表 config/risk_tiers.json (root 配下, 56 本目 §3-1)
    risk_tiers = {
        "_comment": "高リスク（Tier 2）PR 判定用層の表",
        "tiers": {
            "tier2": {
                "CI のワークフロー": [".github/workflows/*.yml"],
                "合否を決めるスクリプト": [".github/scripts/ms4_approval_gate.py", "harness/fastsuite.py"],
                "常駐ワーカー": ["harness/hline_cron.py", "harness/hline.py"],
                "権限": [".claude/", "config/hline.json", "config/protected_paths.json", "harness/hline_room.py", "harness/tool_policy.py"],
                "既存のテスト": ["tests/"],
            }
        }
    }
    (config_dir / "risk_tiers.json").write_text(json.dumps(risk_tiers, indent=2), encoding="utf-8")

    # 5. 部品表 config/arch_components.json (root 配下, 56 本目 §3-3: パスに出ない鍵と title)
    arch_components = {
        "components": {
            "room": {
                "title": "総監督の部屋",
                "modules": ["director_launch.py"],
            }
        }
    }
    (config_dir / "arch_components.json").write_text(json.dumps(arch_components, indent=2), encoding="utf-8")

    # 6. Argon 評価記録 evals.jsonl (55 本目 §3-4: 最後の post レコードを評価対象とする)
    argon_records = [
        {"kind": "post", "target": pr_num, "decision": "HIGH", "rationale": "変更規模大で要警戒"},
    ]
    (argon_dir / "evals.jsonl").write_text(
        "\n".join(json.dumps(r) for r in argon_records) + "\n", encoding="utf-8"
    )

    # 55 本目 §3-5: 本物と同じ鍵 mirror, branches, argon を持つ cfg
    cfg = {
        "mirror": str(mirror_dir),
        "branches": str(branches_dir),
        "argon": str(argon_dir),
    }

    return {
        "cfg": cfg,
        "root": root_dir,
        "mirror_dir": mirror_dir,
        "branches_dir": branches_dir,
        "branch_dir": branch_dir,
        "argon_dir": argon_dir,
    }


class TestPilotBriefInvocation(unittest.TestCase):
    """受入 1: 1 つの命令で 5 行の要約を出力する。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_test_"))
        self.env = make_test_env(self.tmp, pr_num="311", is_high_risk=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_brief(self, argv, cfg=Ellipsis, root=Ellipsis):
        if pilot_brief is None:
            self.fail("harness.pilot_brief がまだ実装されていません")
        out = io.StringIO()
        err = io.StringIO()
        target_cfg = self.env["cfg"] if cfg is Ellipsis else cfg
        target_root = self.env["root"] if root is Ellipsis else root
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = pilot_brief.main(argv, cfg=target_cfg, root=target_root)
        return code, out.getvalue(), err.getvalue()

    def test_missing_branch_copy_exits_with_code_2_and_empty_stdout(self):
        """58 本目 §3-2: 枝の写し（.branch.json）が無いときは stdout が空で終了コード 2、stderr に 1 行。"""
        code, out, err = self.run_brief(["999"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(bool(err.strip()), "stderr に 1 行理由が出ること")
        self.assertIn(".branch.json", err)

    def test_missing_main_mirror_exits_with_code_2_and_empty_stdout(self):
        """58 本目 §3-2: main の写し（.mirror.json）が無いときは stdout が空で終了コード 2、stderr に 1 行。"""
        # .mirror.json を削除
        (self.env["mirror_dir"] / ".mirror.json").unlink()
        code, out, err = self.run_brief(["311"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(bool(err.strip()), "stderr に 1 行理由が出ること")
        self.assertIn(".mirror.json", err)

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
        (self.env["root"] / "docs" / "progress.yaml").write_text(multiline_progress, encoding="utf-8")

        code, out, _ = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 5, f"改行入りでもちょうど 5 行でなければならない: {out}")


class TestPilotBriefMechanicalContent(unittest.TestCase):
    """受入 2: 中身は機械で作られ、仮の出どころの値を変えると出力の行が変わる。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_test_"))
        self.env = make_test_env(self.tmp, pr_num="311", is_high_risk=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_brief(self, argv, cfg=None, root=None):
        if pilot_brief is None:
            self.fail("harness.pilot_brief がまだ実装されていません")
        out = io.StringIO()
        target_cfg = cfg or self.env["cfg"]
        target_root = root or self.env["root"]
        with contextlib.redirect_stdout(out):
            code = pilot_brief.main(argv, cfg=target_cfg, root=target_root)
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
        (self.env["root"] / "docs" / "progress.yaml").write_text(mod_progress, encoding="utf-8")

        # 出どころ 2: Argon 評価記録の rationale を変更
        mod_argon = {
            "kind": "post",
            "target": "311",
            "decision": "HIGH",
            "rationale": "まったく異なる批評テキスト999",
        }
        (self.env["argon_dir"] / "evals.jsonl").write_text(json.dumps(mod_argon) + "\n", encoding="utf-8")

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
            env_low = make_test_env(low_tmp, pr_num="312", is_high_risk=False)
            code, out = self.run_brief(["312"], cfg=env_low["cfg"], root=env_low["root"])
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0], "低リスク：5 行の要約は要らない")
        finally:
            shutil.rmtree(low_tmp, ignore_errors=True)

    def test_added_new_test_remains_low_risk(self):
        """55 本目 §3-2: 新しいテストのファイルを足しただけの枝は低リスク（5 行要約不要）になる。"""
        new_test_tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_new_test_"))
        try:
            env = make_test_env(new_test_tmp, pr_num="313", is_high_risk=False)
            # main に無い新しいテストファイルだけを追加
            (env["branch_dir"] / "tests" / "test_brand_new.py").write_text("# brand new test", encoding="utf-8")
            code, out = self.run_brief(["313"], cfg=env["cfg"], root=env["root"])
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0], "低リスク：5 行の要約は要らない")
        finally:
            shutil.rmtree(new_test_tmp, ignore_errors=True)

    def test_deleted_existing_test_is_high_risk(self):
        """55 本目 §3-1: main にある tests/ のファイルを枝側から消すと高リスクになり、消したことも出る。"""
        del_test_tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_del_test_"))
        try:
            env = make_test_env(del_test_tmp, pr_num="314", is_high_risk=False)
            # main にある tests/test_existing.py を枝側から削除
            os.remove(env["branch_dir"] / "tests" / "test_existing.py")
            code, out = self.run_brief(["314"], cfg=env["cfg"], root=env["root"])
            self.assertEqual(code, 0)
            lines = out.splitlines()
            self.assertEqual(len(lines), 5)
            high_risk_line = lines[1]
            self.assertIn("tests/test_existing.py", high_risk_line)
            # 消したこと（削除）が分かる語が含まれること（56 本目 §4: 出力の語を「削除」に固定）
            self.assertIn(
                "削除",
                high_risk_line,
                f"消したことが分かる表記として '削除' が必要: '{high_risk_line}'",
            )
        finally:
            shutil.rmtree(del_test_tmp, ignore_errors=True)

    def test_high_risk_shows_category_names_and_paths(self):
        """55 本目 §3-3: 高リスクの行にはパスだけでなく当たった区分の名前も出る（複数区分対応）。"""
        multi_tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_multi_"))
        try:
            env = make_test_env(multi_tmp, pr_num="315", is_high_risk=False)
            # CI ワークフローを追加
            wf_dir = env["branch_dir"] / ".github" / "workflows"
            wf_dir.mkdir(parents=True, exist_ok=True)
            (wf_dir / "ci.yml").write_text("# new workflow", encoding="utf-8")

            code, out = self.run_brief(["315"], cfg=env["cfg"], root=env["root"])
            self.assertEqual(code, 0)
            lines = out.splitlines()
            high_risk_line = lines[1]
            # パスと区分の名前の両方が出る
            self.assertIn(".github/workflows/ci.yml", high_risk_line)
            self.assertIn("CI のワークフロー", high_risk_line)
        finally:
            shutil.rmtree(multi_tmp, ignore_errors=True)

    def test_side_effects_and_wiring_reach(self):
        """副作用と配線の到達: 外側パス、harness 部品、未達固定文（56 本目 §3-3: 偽陽性排除）。"""
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        side_line = lines[3]
        # 外側パス (config/sample.json)
        self.assertIn("config/sample.json", side_line)
        # 部品 (パス director_launch.py に出ない title「総監督の部屋」を行に出すことを or なしで検証)
        self.assertIn("総監督の部屋", side_line)
        # 配線到達は未達固定
        self.assertIn("配線の検査は未達（req.wiring-executed）", side_line)

    def test_argon_high_shows_high_prefix(self):
        """55 本目 §3-4: Argon 判定が HIGH なら、批評の行頭（札の直後）に HIGH が見える（甘い判定なし）。"""
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        critique_line = lines[4]
        self.assertTrue(
            critique_line.startswith("レビュアーの批評: HIGH"),
            f"批評行頭は 'レビュアーの批評: HIGH' で始まらなければならない: '{critique_line}'",
        )

    def test_argon_last_post_record_evaluated(self):
        """55 本目 §3-4: kind: post で target がその PR の最後の 1 件を取り、頭が LOW になること。"""
        # 4 件を並べる:
        # 1. 同じ PR の post（HIGH）
        # 2. 別の PR の post（HIGH）
        # 3. 同じ PR の pre（HIGH）
        # 4. 同じ PR の post（LOW）
        records = [
            {"kind": "post", "target": "311", "decision": "HIGH", "rationale": "1件目の古い判定HIGH"},
            {"kind": "post", "target": "312", "decision": "HIGH", "rationale": "別PRの判定HIGH"},
            {"kind": "pre", "target": "311", "decision": "HIGH", "rationale": "事前判定HIGH"},
            {"kind": "post", "target": "#311", "decision": "LOW", "rationale": "最新の事後判定LOW"},
        ]
        (self.env["argon_dir"] / "evals.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
        )

        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        critique_line = lines[4]
        # 行頭は LOW で始まる
        self.assertTrue(
            critique_line.startswith("レビュアーの批評: LOW"),
            f"最新の post 判定 LOW が行頭に見えなければならない: '{critique_line}'",
        )
        self.assertIn("最新の事後判定LOW", critique_line)
        self.assertNotIn("1件目の古い判定HIGH", critique_line)

    def test_missing_sources_handled_gracefully(self):
        """出どころが無いときは「無し（<出どころの名前>）」。55 本目 §4: タスク無し時は検証テスト書き手も無しになる。"""
        # 1. Argon の記録が無い
        (self.env["argon_dir"] / "evals.jsonl").unlink()
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
        (self.env["root"] / "docs" / "progress.yaml").write_text(no_oracle_progress, encoding="utf-8")
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("無し（oracle）", lines[2])

        # 3. 枝名からタスクが引けない: 55 本目 §4 に従い、要旨と理由だけでなく検証テストの書き手も「無し（台帳タスク）」
        unmatched_branch = {
            "sha": "branch_sha_999",
            "branch": "task/W-999-unknown-task",
        }
        (self.env["branch_dir"] / ".branch.json").write_text(
            json.dumps(unmatched_branch), encoding="utf-8"
        )
        code, out = self.run_brief(["311"])
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertIn("無し（台帳タスク）", lines[0])
        self.assertIn("無し（台帳タスク）", lines[2])

    def test_variable_paths_and_components_not_hardcoded(self):
        """55 本目 §4: 出力に出るパス等は写しを変えると変わり、コードに決め打ちがない。"""
        branch_dir = self.env["branch_dir"]
        (branch_dir / "config" / "extra1.json").write_text("{}", encoding="utf-8")
        (branch_dir / "config" / "extra2.json").write_text("{}", encoding="utf-8")

        _, out_more = self.run_brief(["311"])
        self.assertIn("extra1.json", out_more)
        self.assertIn("extra2.json", out_more)


class TestPilotBriefRealConfigAndTable(unittest.TestCase):
    """55 本目 §3-3 & 56 本目 §3-1: 42 本目の教えに基づき、本物の層の表（config/risk_tiers.json）の構造を確かめる。"""

    def test_real_risk_tiers_table(self):
        """本物の層の表が読め、5 区分がすべて存在し、どの区分も空でないこと。"""
        table_path = ROOT / "config" / "risk_tiers.json"
        self.assertTrue(table_path.exists(), f"本物の層の表が存在すること: {table_path}")
        data = json.loads(table_path.read_text(encoding="utf-8"))
        self.assertIn("tiers", data)
        self.assertIn("tier2", data["tiers"])
        tier2 = data["tiers"]["tier2"]

        for category in TIER2_CATEGORIES:
            self.assertIn(category, tier2, f"区分 '{category}' が config/risk_tiers.json に存在すること")
            val = tier2[category]
            self.assertTrue(bool(val), f"区分 '{category}' が空でないこと")

    def test_real_risk_tiers_patterns_match_existing_paths(self):
        """56 本目 §3-1: 本物の表の全パターンが ROOT 下に実在し、主要スクリプトが対応区分に当たること。"""
        table_path = ROOT / "config" / "risk_tiers.json"
        self.assertTrue(table_path.exists(), f"本物の層の表が存在すること: {table_path}")
        data = json.loads(table_path.read_text(encoding="utf-8"))
        tier2 = data["tiers"]["tier2"]

        for category, patterns in tier2.items():
            for pat in patterns:
                if "*" in pat or "?" in pat:
                    matched = list(ROOT.glob(pat))
                    self.assertTrue(
                        len(matched) > 0,
                        f"区分 '{category}' の glob パターン '{pat}' が ROOT 下の実在ファイルに 1 つも当たりません",
                    )
                else:
                    path = ROOT / pat
                    self.assertTrue(
                        path.exists(),
                        f"区分 '{category}' のパス '{pat}' が ROOT の下に実在しません: {path}",
                    )

        # .github/scripts/ms4_approval_gate.py が「合否を決めるスクリプト」に当たる
        self.assertTrue(
            any(fnmatch.fnmatch(".github/scripts/ms4_approval_gate.py", p) for p in tier2["合否を決めるスクリプト"]),
            ".github/scripts/ms4_approval_gate.py が「合否を決めるスクリプト」に当たること",
        )
        # harness/hline_cron.py が「常駐ワーカー」に当たる
        self.assertTrue(
            any(fnmatch.fnmatch("harness/hline_cron.py", p) for p in tier2["常駐ワーカー"]),
            "harness/hline_cron.py が「常駐ワーカー」に当たること",
        )

    def test_load_config_used_when_cfg_not_passed(self):
        """58 本目 §3-1: cfg を渡さずに走らせたとき、hline_base.load_config の返す場所を読むこと。"""
        tmp = Path(tempfile.mkdtemp(prefix="pilot_brief_cfg_test_"))
        try:
            env = make_test_env(tmp, pr_num="311", is_high_risk=False)
            out = io.StringIO()
            err = io.StringIO()
            # load_config を差し替えて一時環境の cfg を返させる
            with mock.patch("harness.pilot_brief.load_config", return_value=env["cfg"]):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = pilot_brief.main(["311"], cfg=None, root=env["root"])
                self.assertEqual(code, 0)
                self.assertEqual(out.getvalue().strip(), "低リスク：5 行の要約は要らない")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestPilotBriefClaudeMdAndRules(unittest.TestCase):
    """受入 3: game-harness の CLAUDE.md の報告の節と、消した決まりの非存在。"""

    def test_claude_md_report_section_has_command_name(self):
        """55 本目 §4 & 56 本目 §3-2: CLAUDE.md 全文ではなく「報告」の節の中に harness.pilot_brief があること。"""
        claude_md_path = ROOT / "CLAUDE.md"
        self.assertTrue(claude_md_path.exists(), "CLAUDE.md が存在すること")
        content = claude_md_path.read_text(encoding="utf-8")

        # 56 本目 §3-2: 報告の節の見出しから次の見出しまでの範囲を切り出し、report_sections を確かめに使う
        report_sections = []
        in_report = False
        for line in content.splitlines():
            if line.startswith("## ") and any(k in line for k in ("進捗", "報告", "report")):
                in_report = True
                continue
            elif line.startswith("## ") and in_report:
                break
            if in_report and line.strip():
                report_sections.append(line.strip())

        self.assertTrue(report_sections, "CLAUDE.md に報告の節が存在すること")

        # report_sections の中に harness.pilot_brief を含む行が存在することを確認
        brief_report_lines = [
            line for line in report_sections if "harness.pilot_brief" in line
        ]
        self.assertTrue(
            brief_report_lines,
            "CLAUDE.md の報告の節の中に harness.pilot_brief を含む行が見つかりません",
        )
        # その行が報告の文脈（PR や 本文、報告）を持つことを見る
        for line in brief_report_lines:
            self.assertTrue(
                any(k in line for k in ("PR", "本文", "報告")),
                f"harness.pilot_brief を含む行が報告の文脈を持ちません: '{line}'",
            )

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
