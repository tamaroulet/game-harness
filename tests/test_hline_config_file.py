"""H ライン：--config で別の設定ファイルを渡す（W-41）。open_pr が false の設定は統合 PR を出さない。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "harness")]
import hline  # noqa: E402
import hline_base  # noqa: E402


def main_with(argv, cfg_seen):
    """poll を差し替えて main を走らせ、poll が受け取った設定を cfg_seen に集める。"""
    def poll(cfg):
        cfg_seen.append(cfg)
        return 0
    with mock.patch.object(hline, "poll", poll), mock.patch.object(hline.hline_halt, "settle"):
        return hline.main(argv)


class ConfigFlag(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_the_given_file_is_the_config_the_line_runs_with(self):
        base = json.loads(hline_base.CONFIG.read_text(encoding="utf-8"))
        own = {"inbox": str(self.tmp / "inbox"), "out": str(self.tmp / "out"), "worktrees": str(self.tmp / "wt"),
               "base": "bench-base", "integration_branch": "bench/run-1", "milestones": ["W-99"], "open_pr": False}
        path = self.tmp / "bench.json"
        path.write_text(json.dumps({**base, **own}), encoding="utf-8")
        seen = []
        self.assertEqual(main_with(["poll", "--config", str(path)], seen), 0)
        self.assertEqual(len(seen), 1)
        for key, value in own.items():
            with self.subTest(key=key):
                self.assertEqual(seen[0][key], value)

    def test_without_the_flag_the_default_config_is_read(self):
        seen = []
        with mock.patch.object(hline, "load_config", return_value={"marker": 1}) as load:
            main_with(["poll"], seen)
        load.assert_called_once_with()
        self.assertEqual(seen, [{"marker": 1}])

    def test_the_flag_reaches_load_config_for_setup_too(self):
        with mock.patch.object(hline, "load_config", return_value={}) as load, \
                mock.patch.object(hline, "setup", return_value=0) as setup:
            self.assertEqual(hline.main(["setup", "--config", "x.json"]), 0)
        load.assert_called_once_with("x.json")
        setup.assert_called_once_with({})


class OpenPrSwitch(unittest.TestCase):
    def run_line(self, cfg):
        st = {"items": {"a": {"status": "done"}}, "awaiting_pr": None}
        names = {"load_state": st, "save_state": None, "sweep": None, "fetch": None, "check_code": False,
                 "recover": None, "refresh": None, "intake": None, "mark": None, "write_report": None,
                 "next_runnable": None, "only_orphans_wait": True, "ahead": 1, "diff_files": [],
                 "problems": [], "pr_title": "t", "pr_body": "b", "open_pr": None, "create_pr": "https://example.invalid/pr/1"}
        patches = {n: mock.patch.object(hline, n, return_value=v) for n, v in names.items()}
        patches["quota"] = mock.patch.object(hline.hline_quota, "waiting", return_value=False)
        mocks = {}
        for n, p in patches.items():
            mocks[n] = p.start()
            self.addCleanup(p.stop)
        code = hline.run_line(cfg)
        return code, st, mocks

    def test_false_stacks_everything_but_opens_no_pr(self):
        code, st, mocks = self.run_line({"open_pr": False})
        self.assertEqual(code, 0)
        mocks["open_pr"].assert_not_called()
        mocks["create_pr"].assert_not_called()
        self.assertIsNone(st["awaiting_pr"])
        mocks["write_report"].assert_called()

    def test_a_missing_field_or_true_opens_the_pr_as_before(self):
        for cfg in ({}, {"open_pr": True}):
            with self.subTest(cfg=cfg):
                code, st, mocks = self.run_line(cfg)
                self.assertEqual(code, 0)
                mocks["create_pr"].assert_called_once()
                self.assertEqual(st["awaiting_pr"], "https://example.invalid/pr/1")


if __name__ == "__main__":
    unittest.main()
