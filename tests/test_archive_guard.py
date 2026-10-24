"""過去ログ読み込みの保護テスト（総監督 21 本目 §3・28 本目 §4、W-177）。

現役台帳（docs/progress.yaml）に archive 宣言があるとき、
過去ログが読めなければ ProgressError を送出し、黙って現役だけの台帳を返さない
（フェイルクローズド）ことを厳格に検証する。
"""
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
                if progress.REL_PATH in cmd_str:
                    return 0, self.active_text, ""
                if progress.ARCHIVE_REL_PATH in cmd_str:
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

            # 4. goal_distance.load_ledger
            import goal_distance as gd
            ref, st_gd = gd.load_ledger(cfg)
            self.assertIn(ref, ("origin/hline/integration", "origin/main"))
            self.assertTrue(any(t["id"] == "W-122" for t in st_gd["tasks"]))

            # 5. hline_ledger_entry.base_state
            st_base = hline_ledger_entry.base_state(cfg)
            self.assertIsNotNone(st_base)
            self.assertTrue(any(t["id"] == "W-122" for t in st_base["tasks"]))

            # 6. hline_ledger_entry.on_integration
            with mock.patch.object(hline_git, "integration_exists", return_value=True):
                self.assertTrue(hline_ledger_entry.on_integration(cfg, "W-122"))

            # 7. hline_report.progress_report
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
                if progress.REL_PATH in cmd_str:
                    return 0, self.active_text, ""
                if progress.ARCHIVE_REL_PATH in cmd_str:
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

            # 5. hline_ledger_entry.on_integration は ProgressError を送出する（False を返さずフェイルクローズド）
            with mock.patch.object(hline_git, "integration_exists", return_value=True):
                with self.assertRaises(progress.ProgressError):
                    hline_ledger_entry.on_integration(cfg, "W-122")

            # 6. hline_report.progress_report は ProgressError を送出する（黙って現役だけを返さずフェイルクローズド）
            with self.assertRaises(progress.ProgressError):
                hline_report.progress_report(cfg)
        finally:
            proc.run = orig_proc_run


if __name__ == "__main__":
    unittest.main()
