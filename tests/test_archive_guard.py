"""過去ログ読み込みの保護テスト（総監督 21 本目 §3・28 本目 §4、W-177）。

現役台帳（docs/progress.yaml）に archive 宣言があるとき、
過去ログが読めなければ ProgressError を送出し、黙って現役だけの台帳を返さない
（フェイルクローズド）ことを厳格に検証する。
"""
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import progress  # noqa: E402
import goal_distance  # noqa: E402
import hline_queue  # noqa: E402
import hline_stopped  # noqa: E402
import hline_ledger_entry  # noqa: E402
import hline_report  # noqa: E402
import hline_git  # noqa: E402


class ArchiveGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.active_path = ROOT / progress.REL_PATH
        cls.archive_path = ROOT / progress.ARCHIVE_REL_PATH
        cls.active_text = cls.active_path.read_text(encoding="utf-8")
        cls.archive_text = cls.archive_path.read_text(encoding="utf-8")

        # archive 宣言のない古い台帳テキストのシミュレーション
        active_state = yaml.safe_load(cls.active_text)
        legacy_state = dict(active_state)
        legacy_state.pop("archive", None)
        cls.legacy_text = yaml.safe_dump(legacy_state, allow_unicode=True, sort_keys=False)

    def test_read_ref_failclosed_when_archive_missing(self):
        """台帳に archive 宣言があるのに過去ログが読めなければ ProgressError を送出する。"""
        def show_fn_missing_archive(rel_path):
            if rel_path == progress.REL_PATH:
                return self.active_text
            if rel_path == progress.ARCHIVE_REL_PATH:
                return None
            return None

        with self.assertRaises(progress.ProgressError) as ctx:
            progress.read_ref(show_fn_missing_archive)
        self.assertIn("過去ログ宣言", str(ctx.exception))

    def test_read_ref_failclosed_when_archive_corrupted(self):
        """過去ログが壊れている（辞書でない、tasks がない）とき ProgressError を送出する。"""
        def show_fn_corrupted(rel_path):
            if rel_path == progress.REL_PATH:
                return self.active_text
            if rel_path == progress.ARCHIVE_REL_PATH:
                return "invalid: [yaml: broken"
            return None

        with self.assertRaises(Exception):
            progress.read_ref(show_fn_corrupted)

        def show_fn_no_tasks(rel_path):
            if rel_path == progress.REL_PATH:
                return self.active_text
            if rel_path == progress.ARCHIVE_REL_PATH:
                return "foo: bar\n"
            return None

        with self.assertRaises(progress.ProgressError) as ctx:
            progress.read_ref(show_fn_no_tasks)
        self.assertIn("形式が不正", str(ctx.exception))

    def test_read_ref_backward_compatible_without_archive_declaration(self):
        """archive 宣言の無い古い台帳は、過去ログが無くても例外を出さず現役のみを返す。"""
        def show_fn_legacy(rel_path):
            if rel_path == progress.REL_PATH:
                return self.legacy_text
            return None

        st = progress.read_ref(show_fn_legacy)
        self.assertIsNotNone(st)
        self.assertNotIn("archive", st)
        self.assertEqual(len(st["tasks"]), len(yaml.safe_load(self.legacy_text)["tasks"]))

    def test_load_failclosed_when_archive_missing_in_isolated_directory(self):
        """一時ディレクトリの台帳が過去ログを宣言し、その過去ログが無い場合、
        ROOT 側に過去ログがあってもフォールバックせず ProgressError を送出する。
        """
        self.assertTrue((progress.ROOT / progress.ARCHIVE_REL_PATH).exists())
        with tempfile.TemporaryDirectory() as d:
            tmp_root = Path(d)
            docs_dir = tmp_root / "docs"
            docs_dir.mkdir(parents=True)
            active_path = docs_dir / "progress.yaml"
            active_path.write_text(self.active_text, encoding="utf-8")
            # tmp_root には docs/progress_archive.yaml は置かない
            with self.assertRaises(progress.ProgressError) as ctx:
                progress.load(active_path)
            self.assertIn("過去ログ宣言", str(ctx.exception))

    def test_subcomponents_include_completed_tasks_when_both_present(self):
        """偽の git show が 2 つのファイルを返したとき、主要コンポーネントが過去ログのタスクを含む。"""
        def show_fn_both(rel_path):
            if rel_path == progress.REL_PATH:
                return self.active_text
            if rel_path == progress.ARCHIVE_REL_PATH:
                return self.archive_text
            return None

        # 1. progress.read_ref
        st = progress.read_ref(show_fn_both)
        self.assertIsNotNone(st)
        arch_tasks = yaml.safe_load(self.archive_text)["tasks"]
        self.assertGreater(len(st["tasks"]), len(arch_tasks))
        self.assertTrue(any(t["id"] == "W-122" and t["status"] == "completed" for t in st["tasks"]))

        # 2. hline_queue.read_ledger
        cfg = {"base": "origin/main", "integration_branch": "hline/integration", "ttl_seconds": {"git": 10}}
        import proc
        orig_proc_run = proc.run
        try:
            def fake_proc_run(args, cwd, ttl, label):
                cmd_str = " ".join(args)
                if f"origin/main:{progress.REL_PATH}" in cmd_str:
                    return 0, self.active_text, ""
                if f"origin/main:{progress.ARCHIVE_REL_PATH}" in cmd_str:
                    return 0, self.archive_text, ""
                return 1, "", "not found"

            proc.run = fake_proc_run
            ledger = hline_queue.read_ledger(cfg)
            self.assertIn("W-122", ledger)
            self.assertTrue(ledger["W-122"]["done"])

            # 3. hline_stopped.main_ledger
            st_stopped, err = hline_stopped.main_ledger(cfg, fake_proc_run)
            self.assertIsNone(err)
            self.assertIsNotNone(st_stopped)
            self.assertTrue(any(t["id"] == "W-122" for t in st_stopped["tasks"]))

            # 4. goal_distance.load_ledger (origin/main へのフォールバックを厳密に検証)
            import goal_distance as gd
            ref, st_gd = gd.load_ledger(cfg)
            self.assertEqual(ref, "origin/main")
            self.assertTrue(any(t["id"] == "W-122" for t in st_gd["tasks"]))

            # 5. hline_ledger_entry.base_state
            st_base = hline_ledger_entry.base_state(cfg)
            self.assertIsNotNone(st_base)
            self.assertTrue(any(t["id"] == "W-122" for t in st_base["tasks"]))

            # 6. hline_report.progress_report (origin/main 経由で正常取得)
            rep = hline_report.progress_report(cfg)
            self.assertIn("W-122", rep)
        finally:
            proc.run = orig_proc_run

    def test_subcomponents_failclosed_when_archive_fails(self):
        """過去ログだけが読めないとき、主要コンポーネントが黙って現役だけを返さずフェイルクローズドに失敗する。"""
        cfg = {"base": "origin/main", "integration_branch": "hline/integration", "ttl_seconds": {"git": 10}}
        import proc
        orig_proc_run = proc.run
        try:
            def fake_proc_run_archive_fail(args, cwd, ttl, label):
                cmd_str = " ".join(args)
                if f"origin/main:{progress.REL_PATH}" in cmd_str:
                    return 0, self.active_text, ""
                if f"origin/main:{progress.ARCHIVE_REL_PATH}" in cmd_str:
                    return 1, "", "git show failed"
                return 1, "", "not found"

            proc.run = fake_proc_run_archive_fail

            # 1. hline_queue.read_ledger は ValueError を送出する
            with self.assertRaises(ValueError):
                hline_queue.read_ledger(cfg)

            # 2. hline_stopped.main_ledger は (None, "...") を返す
            st_stopped, err = hline_stopped.main_ledger(cfg, fake_proc_run_archive_fail)
            self.assertIsNone(st_stopped)
            self.assertIn("ProgressError", err)

            # 3. goal_distance.load_ledger は Unreadable を送出する
            import goal_distance as gd
            with self.assertRaises(gd.Unreadable):
                gd.load_ledger(cfg)

            # 4. hline_ledger_entry.base_state は ValueError を送出する（ProgressError を包む契約）
            with self.assertRaises(ValueError) as ctx_base:
                hline_ledger_entry.base_state(cfg)
            self.assertIsInstance(ctx_base.exception.__cause__, progress.ProgressError)

            # 5. hline_report.progress_report は ProgressError を送出する（黙って現役だけを返さずフェイルクローズド）
            with self.assertRaises(progress.ProgressError):
                hline_report.progress_report(cfg)
        finally:
            proc.run = orig_proc_run

    def test_on_integration_with_archive(self):
        """統合ブランチの git show が過去ログを含むとき True、過去ログだけ失敗するとき ProgressError。"""
        cfg = {"base": "origin/main", "integration_branch": "hline/integration", "ttl_seconds": {"git": 10}}
        import proc
        orig_proc_run = proc.run
        try:
            # 正常時
            def fake_proc_run_success(args, cwd, ttl, label):
                cmd_str = " ".join(args)
                if f"origin/hline/integration:{progress.REL_PATH}" in cmd_str:
                    return 0, self.active_text, ""
                if f"origin/hline/integration:{progress.ARCHIVE_REL_PATH}" in cmd_str:
                    return 0, self.archive_text, ""
                return 1, "", "not found"

            proc.run = fake_proc_run_success
            with mock.patch.object(hline_git, "integration_exists", return_value=True):
                self.assertTrue(hline_ledger_entry.on_integration(cfg, "W-122"))

            # 過去ログだけ失敗時: False ではなく ProgressError を送出（フェイルクローズド）
            def fake_proc_run_fail(args, cwd, ttl, label):
                cmd_str = " ".join(args)
                if f"origin/hline/integration:{progress.REL_PATH}" in cmd_str:
                    return 0, self.active_text, ""
                if f"origin/hline/integration:{progress.ARCHIVE_REL_PATH}" in cmd_str:
                    return 1, "", "git show failed"
                return 1, "", "not found"

            proc.run = fake_proc_run_fail
            with mock.patch.object(hline_git, "integration_exists", return_value=True):
                with self.assertRaises(progress.ProgressError):
                    hline_ledger_entry.on_integration(cfg, "W-122")
        finally:
            proc.run = orig_proc_run

    def test_intake_keeps_what_in_inbox_and_clean_queue_when_archive_fails(self):
        """統合ブランチの過去ログが読めないとき、intake は例外で落ちず、
        What を受信箱に残し、queue.json (st['items']) に追加しない。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inbox = root / "inbox"
            inbox.mkdir(parents=True)
            cfg = {
                "inbox": str(inbox), "root": str(root), "out": str(root / "out"),
                "items": str(root / "out" / "queue"), "base": "origin/main",
                "milestones": ["G"]
            }
            st = {"items": {}, "skipped": []}

            # 新規タスク項目を持つ What
            entry = {
                "id": "W-999", "title": "テスト", "group": "G", "target_repo": "game-harness",
                "status": "pending", "by": "hline", "purpose": "P", "deliverable": "D",
                "serves": "R9", "rung": "R9", "acceptance": ["A"],
                "oracle": [{"path": "tests/test_x.py", "from": "existing"}],
                "verification": {"command": "python -m unittest tests.test_x", "expected_exit_code": 0}
            }
            what_content = (
                "# W-999 テスト\n\n"
                "マイルストーン: G\n"
                "タスク: W-999\n\n"
                "## What\n本文\n\n"
                "## 合格条件\n- 条件 1\n\n"
                "## 台帳の項目\n```yaml\n"
                + yaml.safe_dump(entry, allow_unicode=True)
                + "```\n"
            )
            what_file = inbox / "W-999.md"
            what_file.write_text(what_content, encoding="utf-8")

            # 監修役レビューとオラクル検査は合格させる
            import hline_review
            with mock.patch.object(hline_queue, "read_ledger", return_value={}), \
                    mock.patch.object(hline_ledger_entry, "check", return_value=(entry, [])), \
                    mock.patch.object(hline_review, "review", return_value=None), \
                    mock.patch.object(hline_ledger_entry, "stack", side_effect=progress.ProgressError("過去ログが読めません")):
                taken = hline_queue.intake(cfg, st)

            # 1. intake は落ちずに taken は空
            self.assertEqual(taken, [])
            # 2. What ファイルは inbox に残っている（unlink されない）
            self.assertTrue(what_file.exists())
            # 3. st['items'] に追加されていない
            self.assertNotIn("W-999", st["items"])
            # 4. st['skipped'] に理由が記録されている
            self.assertTrue(any("過去ログ" in s.get("reason", "") for s in st["skipped"]))

    def test_write_report_warns_and_avoids_silent_corruption_when_archive_fails(self):
        """過去ログだけが読めないとき、write_report は本物の progress_report・各節を回しても
        クラッシュせず、report.md を警告と理由・人間作業で上書きし、古い報告を残さない。
        """
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            inbox = root / "inbox"
            inbox.mkdir(parents=True)
            report_md = inbox / "report.md"
            report_md.write_text("## 古い正常な報告\n", encoding="utf-8")
            cfg = {
                "inbox": str(inbox), "root": str(root), "base": "origin/main",
                "integration_branch": "hline/integration",
                "ttl_seconds": {"git": 10}, "out": str(root / "out")
            }
            # 人間作業が残ることも確かめるため awaiting_pr を設定
            st = {"items": {}, "awaiting_pr": "https://github.com/owner/repo/pull/123", "skipped": []}

            import proc
            orig_proc_run = proc.run
            try:
                def fake_proc_run_archive_fail(args, cwd, ttl, label):
                    cmd_str = " ".join(args)
                    if f"origin/main:{progress.REL_PATH}" in cmd_str:
                        return 0, self.active_text, ""
                    if f"origin/main:{progress.ARCHIVE_REL_PATH}" in cmd_str:
                        return 1, "", "git show failed"
                    return 1, "", "not found"

                proc.run = fake_proc_run_archive_fail

                # 外に出る副作用・環境依存（照合・probe）のみモックし、台帳や各節は本物を回す
                with mock.patch.object(hline_report.hline_probe, "safe_section", return_value="## probe\n"), \
                        mock.patch.object(hline_report.hline_checks, "safe_section", return_value="## checks\n"):
                    hline_report.write_report(cfg, st)
            finally:
                proc.run = orig_proc_run

            body = report_md.read_text(encoding="utf-8")
            # 1. 警告見出しと過去ログ読めない理由が出力されている
            self.assertIn("## ⚠️ 台帳（過去ログ）を読めません", body)
            self.assertIn("過去ログ宣言 docs/progress_archive.yaml がありますが、ファイルを読み込めませんでした", body)
            # 2. 古い報告が残っていない（上書きされている）
            self.assertNotIn("## 古い正常な報告", body)
            # 3. 人間作業の行が警告の後ろに残っている
            self.assertIn("- 人間作業: REVIEW_REQUIRED https://github.com/owner/repo/pull/123", body)
            # 4. 各節（目的までの距離、関門ごとの流れ、Hライン）が本物で生成されている
            self.assertIn("## 目的までの距離", body)
            self.assertIn("## 関門ごとの流れ", body)
            self.assertIn("## H ライン", body)


if __name__ == "__main__":
    unittest.main()
