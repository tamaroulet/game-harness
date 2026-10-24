"""総監督が読む main の写し（W-150。受入は総監督：受信箱の drafts/director/reply-architect-20261023-6.md）。

    python -m unittest tests.test_main_mirror

見る点（総監督の「試験が見ること」を削らない）:
1. 偽の Git のリポジトリで、命令の後に写しの HEAD と記録の SHA が origin/main と同じ。写しに置いた変更が消える
2. 渡した作業コピーのパスに書き込まない
3. 記録の SHA と HEAD が違うと、report.md の冒頭に「写しが古い」が出る
4. setup が書く settings に、写しへの Edit の拒否があり、Read の拒否が無い。CLAUDE.md に写しのパスがある
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import hline_report  # noqa: E402
import hline_room  # noqa: E402
import main_mirror  # noqa: E402
import proc  # noqa: E402
from tests import test_report_stopped as stopped  # noqa: E402

ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True, capture_output=True, text=True).stdout.strip()


def snapshot(path):
    """作業コピーの、.git を含むすべてのファイルの（相対パス, 大きさ, 更新時刻）。"""
    return sorted((str(p.relative_to(path)), p.stat().st_size, p.stat().st_mtime_ns)
                  for p in Path(path).rglob("*") if p.is_file())


class Mirror(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.origin, self.human, self.feeder = self.tmp / "origin.git", self.tmp / "human", self.tmp / "feeder"
        git("init", "-q", "--bare", "-b", "main", str(self.origin), cwd=self.tmp)
        git("clone", "-q", str(self.origin), str(self.feeder), cwd=self.tmp)
        self.commit("a.txt", "1")
        git("clone", "-q", str(self.origin), str(self.human), cwd=self.tmp)
        (self.human / "local-edit.txt").write_text("人の作業", encoding="utf-8")
        self.cfg = json.loads(json.dumps(stopped.CFG))
        self.cfg.pop("branches", None)
        self.cfg["mirror"] = str(self.tmp / "mirror")

    def commit(self, name, text):
        (self.feeder / name).write_text(text, encoding="utf-8")
        git("add", "-A", cwd=self.feeder)
        git("commit", "-q", "-m", f"{name} {text}", cwd=self.feeder)
        git("push", "-q", "origin", "HEAD:main", cwd=self.feeder)
        return git("rev-parse", "HEAD", cwd=self.feeder)

    def sync(self):
        return main_mirror.sync(self.cfg, root=self.human, clock=stopped.CLOCK)

    # 1
    def test_sync_matches_origin_main_and_drops_changes_in_the_mirror(self):
        rec = self.sync()
        mirror = Path(self.cfg["mirror"])
        self.assertEqual(rec["sha"], git("rev-parse", "main", cwd=self.origin))
        head = self.commit("b.txt", "2")
        (mirror / "a.txt").write_text("総監督が変えた", encoding="utf-8")
        (mirror / "stray.txt").write_text("置いた", encoding="utf-8")
        (mirror / "ignored").mkdir()
        rec = self.sync()
        self.assertEqual(git("rev-parse", "HEAD", cwd=mirror), head)
        self.assertEqual(rec["sha"], head)
        self.assertEqual(json.loads((mirror / main_mirror.RECORD).read_text(encoding="utf-8")), rec)
        self.assertEqual((mirror / "a.txt").read_text(encoding="utf-8"), "1")
        self.assertFalse((mirror / "stray.txt").exists())
        self.assertFalse((mirror / "ignored").exists())
        self.assertEqual([l for l in git("status", "--porcelain", cwd=mirror).splitlines() if main_mirror.RECORD not in l], [])

    # 2
    def test_the_working_copy_is_not_written(self):
        before = snapshot(self.human)
        self.sync()
        self.commit("b.txt", "2")
        self.sync()
        self.assertEqual(snapshot(self.human), before)

    def test_a_non_git_folder_at_the_mirror_is_kept_and_refused(self):
        mirror = Path(self.cfg["mirror"])
        mirror.mkdir()
        (mirror / "keep.txt").write_text("消さない", encoding="utf-8")
        with self.assertRaises(main_mirror.MirrorError):
            self.sync()
        self.assertEqual((mirror / "keep.txt").read_text(encoding="utf-8"), "消さない")

    # 3
    def test_stale_record_shows_at_the_top_of_report_md(self):
        rec = self.sync()
        newer = "f" * 40
        self.assertIn(f"（写しが古い：origin/main は {newer}）", main_mirror.line(self.cfg, newer))
        self.assertNotIn("写しが古い", main_mirror.line(self.cfg, rec["sha"]))

        def runner(args, cwd, ttl, label, env=None, input=None):
            return (0, newer + "\n", "") if args[:2] == ["git", "rev-parse"] else stopped.fake_run(args, cwd, ttl, label, env, input)
        top = main_mirror.section(self.cfg, runner).splitlines()
        self.assertEqual(top[0], "## main の写し")
        self.assertIn("写しが古い", top[1])

    def test_no_record_is_stale(self):
        self.assertIn("写しが古い", main_mirror.line(self.cfg, "f" * 40))

    def test_a_broken_record_is_treated_as_none(self):
        mirror = Path(self.cfg["mirror"])
        mirror.mkdir()
        for text in ("{}", "null", "[1]", '{"sha": 1, "at": "x"}', "{"):
            with self.subTest(text=text):
                (mirror / main_mirror.RECORD).write_text(text, encoding="utf-8")
                self.assertIn("- main の写し: なし（写しが古い", main_mirror.line(self.cfg, "f" * 40))

    def test_the_running_report_md_starts_with_the_stale_mirror(self):
        self.sync()
        newer = self.commit("b.txt", "2")
        tmp = self.tmp / "inbox"
        tmp.mkdir()
        self.cfg.update(inbox=str(tmp), out=str(self.tmp / "out"))
        st = {"items": {}, "awaiting_pr": None, "skipped": []}
        stubs = {"progress_report": "## 進捗ツリー\n- 人間作業: NONE\n", "goal_section": "## 目的までの距離\n", "flow_section": "## 関門\n",
                 "h_section": "## H ライン\n", "human_line": None}
        with contextlib.ExitStack() as stack:
            for name, value in stubs.items():
                stack.enter_context(mock.patch.object(hline_report, name, return_value=value))
            stack.enter_context(mock.patch.object(hline_report.hline_probe, "safe_section", return_value="## probe\n"))
            stack.enter_context(mock.patch.object(hline_report.hline_checks, "safe_section", return_value="## 照合\n"))
            stack.enter_context(mock.patch.object(main_mirror, "base_head", return_value=newer))
            hline_report.write_report(self.cfg, st)
        top = (tmp / "report.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(top[0], "## main の写し")
        self.assertIn(f"（写しが古い：origin/main は {newer}）", top[1])


    def test_mirror_at_or_overlapping_root_raises_mirror_error(self):
        before = snapshot(self.human)
        self.cfg["mirror"] = str(self.human)
        with self.assertRaises(main_mirror.MirrorError):
            main_mirror.sync(self.cfg, root=self.human)
        self.assertEqual(snapshot(self.human), before)
        self.cfg["mirror"] = str(self.human.parent)
        with self.assertRaises(main_mirror.MirrorError):
            main_mirror.sync(self.cfg, root=self.human)
        self.assertEqual(snapshot(self.human), before)
        self.cfg["mirror"] = str(self.human / "nested")
        with self.assertRaises(main_mirror.MirrorError):
            main_mirror.sync(self.cfg, root=self.human)
        self.assertEqual(snapshot(self.human), before)

    def test_sync_branches_mirrors_open_prs_and_removes_closed(self):
        branches_dir = self.tmp / "branches"
        self.cfg["branches"] = str(branches_dir)
        git("checkout", "-q", "-b", "feat-101", cwd=self.feeder)
        (self.feeder / "f1.txt").write_text("pr 101", encoding="utf-8")
        git("add", "-A", cwd=self.feeder)
        git("commit", "-q", "-m", "feat-101", cwd=self.feeder)
        git("push", "-q", "origin", "feat-101", cwd=self.feeder)
        sha1 = git("rev-parse", "HEAD", cwd=self.feeder)
        git("checkout", "-q", "main", cwd=self.feeder)

        git("checkout", "-q", "-b", "feat-102", cwd=self.feeder)
        (self.feeder / "f2.txt").write_text("pr 102", encoding="utf-8")
        git("add", "-A", cwd=self.feeder)
        git("commit", "-q", "-m", "feat-102", cwd=self.feeder)
        git("push", "-q", "origin", "feat-102", cwd=self.feeder)
        sha2 = git("rev-parse", "HEAD", cwd=self.feeder)
        git("checkout", "-q", "main", cwd=self.feeder)

        open_prs = [
            {"number": 101, "headRefName": "feat-101", "headRefOid": sha1},
            {"number": 102, "headRefName": "feat-102", "headRefOid": sha2},
        ]
        main_mirror.sync(self.cfg, root=self.human, clock=stopped.CLOCK, open_prs=open_prs)
        self.assertTrue((branches_dir / "101").exists())
        self.assertTrue((branches_dir / "102").exists())
        self.assertEqual(git("rev-parse", "HEAD", cwd=branches_dir / "101"), sha1)
        self.assertEqual(git("rev-parse", "HEAD", cwd=branches_dir / "102"), sha2)
        rec101 = json.loads((branches_dir / "101" / main_mirror.RECORD_BRANCH).read_text(encoding="utf-8"))
        self.assertEqual(rec101["number"], 101)
        self.assertEqual(rec101["sha"], sha1)

        open_prs_closed = [
            {"number": 102, "headRefName": "feat-102", "headRefOid": sha2},
        ]
        main_mirror.sync(self.cfg, root=self.human, clock=stopped.CLOCK, open_prs=open_prs_closed)
        self.assertFalse((branches_dir / "101").exists())
        self.assertTrue((branches_dir / "102").exists())


class StoppedReportTop(stopped.Base):
    """止まっている間の report.md の冒頭（## 書き出し）に、写しの行が出る。"""

    def setUp(self):
        super().setUp()
        self.cfg.pop("branches", None)

    def test_stale_mirror_in_the_stopped_report(self):
        mirror = self.tmp / "mirror"
        mirror.mkdir()
        (mirror / main_mirror.RECORD).write_text(json.dumps({"sha": "a" * 40, "at": "2026-10-23 20:00", "base": "origin/main"}),
                                                  encoding="utf-8")
        self.cfg["mirror"] = str(mirror)

        def runner(args, cwd, ttl, label, env=None, input=None):
            return (0, stopped.SHA + "\n", "") if args[:2] == ["git", "rev-parse"] else stopped.fake_run(args, cwd, ttl, label, env, input)
        self.put_state()
        stopped.hline_stopped.stop(self.cfg, "立て直しの間は止める")
        text = stopped.hline_stopped.write(self.cfg, runner=runner, clock=stopped.CLOCK)
        head = text.split("\n## ", 1)[0]
        self.assertIn(f"- main の写し: {'a' * 40}（合わせた時刻: 2026-10-23 20:00", head)
        self.assertIn(f"（写しが古い：origin/main は {stopped.SHA}）", head)

    def test_the_stopped_report_syncs_the_mirror_before_writing(self):
        origin, feeder, mirror = self.tmp / "origin.git", self.tmp / "feeder", self.tmp / "mirror"
        git("init", "-q", "--bare", "-b", "main", str(origin), cwd=self.tmp)
        git("clone", "-q", str(origin), str(feeder), cwd=self.tmp)
        (feeder / "a.txt").write_text("1", encoding="utf-8")
        git("add", "-A", cwd=feeder)
        git("commit", "-q", "-m", "1", cwd=feeder)
        git("push", "-q", "origin", "HEAD:main", cwd=feeder)
        git("clone", "-q", str(origin), str(mirror), cwd=self.tmp)   # 写しはある。origin は偽（ネットに出ない）
        (feeder / "a.txt").write_text("2", encoding="utf-8")
        git("commit", "-q", "-am", "2", cwd=feeder)
        git("push", "-q", "origin", "HEAD:main", cwd=feeder)
        head = git("rev-parse", "HEAD", cwd=feeder)
        self.cfg["mirror"] = str(mirror)

        def runner(args, cwd, ttl, label, env=None, input=None):   # 写しの中の git だけ本物で走らせる
            if args[:3] == ["git", "-C", str(mirror)]:
                return proc.run(args, cwd, ttl, label)
            if args[:2] == ["git", "rev-parse"]:
                return 0, head + "\n", ""
            return stopped.fake_run(args, cwd, ttl, label, env, input)
        self.put_state()
        stopped.hline_stopped.stop(self.cfg, "立て直しの間は止める")
        text = stopped.hline_stopped.write(self.cfg, runner=runner, clock=stopped.CLOCK, sync=True)
        top = text.split("\n## ", 1)[0]
        self.assertIn(f"- main の写し: {head}（合わせた時刻: 2026-10-23 21:05", top)
        self.assertNotIn("写しが古い", top)
        self.assertNotIn("失敗", top)
        self.assertEqual((mirror / "a.txt").read_text(encoding="utf-8"), "2")

    def test_sync_failure_is_written_not_raised(self):
        self.cfg["mirror"] = str(self.tmp / "mirror")
        self.put_state()
        stopped.hline_stopped.stop(self.cfg, "立て直しの間は止める")
        text = stopped.hline_stopped.write(self.cfg, runner=stopped.fake_run, clock=stopped.CLOCK, sync=True)
        self.assertIn("（合わせるのに失敗:", text.split("\n## ", 1)[0])


class RoomSettings(unittest.TestCase):
    # 4
    def test_settings_deny_edit_but_not_read_of_the_mirror(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        src = tmp / "src"
        inbox = src / ".local" / "inbox"
        inbox.mkdir(parents=True)
        mirror = src / ".local" / "main"   # setup の時点に無い
        deny = hline_room.director_settings(inbox, src_root=src, mirror=mirror)["permissions"]["deny"]
        rule = "//" + mirror.as_posix().replace(":", "", 1).lower() + "/**"
        self.assertIn(f"Edit({rule})", deny)
        self.assertFalse([d for d in deny if d.startswith("Read(") and rule[:-3] in d])
        mirror.mkdir()   # 有っても 1 つだけ
        deny = hline_room.director_settings(inbox, src_root=src, mirror=mirror)["permissions"]["deny"]
        self.assertEqual(deny.count(f"Edit({rule})"), 1)

    def test_settings_deny_edit_but_not_read_of_branches(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        src = tmp / "src"
        inbox = src / ".local" / "inbox"
        inbox.mkdir(parents=True)
        branches = src / ".local" / "branches"
        deny = hline_room.director_settings(inbox, src_root=src, branches=branches)["permissions"]["deny"]
        rule = "//" + branches.as_posix().replace(":", "", 1).lower() + "/**"
        self.assertIn(f"Edit({rule})", deny)
        self.assertFalse([d for d in deny if d.startswith("Read(") and rule[:-3] in d])

    def test_setup_writes_the_mirror_rule_and_claude_md_names_it(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = json.loads(json.dumps(stopped.CFG))
        cfg.update(inbox=str(tmp / "inbox"), out=str(tmp / "out"), mirror=str(tmp / "main"), branches=str(tmp / "branches"))
        with mock.patch.object(hline_room, "write_report"), contextlib.redirect_stdout(io.StringIO()):
            hline_room.setup(cfg)
        deny = json.loads((tmp / "inbox" / ".claude" / "settings.json").read_text(encoding="utf-8"))["permissions"]["deny"]
        rule = "//" + (tmp / "main").as_posix().replace(":", "", 1).lower() + "/**"
        rule_b = "//" + (tmp / "branches").as_posix().replace(":", "", 1).lower() + "/**"
        self.assertIn(f"Edit({rule})", deny)
        self.assertIn(f"Edit({rule_b})", deny)
        self.assertFalse([d for d in deny if d.startswith("Read(") and rule[:-3] in d])
        self.assertEqual(json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))["mirror"], "${local}/main")
        local = json.loads((ROOT / "config" / "machine.example.json").read_text(encoding="utf-8"))["paths"]["local"]
        self.assertIn(f"main の写し（`{local}\\main`）を読む", (tmp / "inbox" / "CLAUDE.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
