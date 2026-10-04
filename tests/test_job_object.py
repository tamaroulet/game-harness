"""Job Object で、子と孫以下まで止める（harness/job_object.py）。Windows の API に依存する検査は他の OS では飛ばす。"""
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))
import exitcode  # noqa: E402
import job_object  # noqa: E402
import proc  # noqa: E402
import scheduler  # noqa: E402

# 孫を起こし、子と孫の pid を argv[1] に書いて眠る
SPAWN = ("import os,subprocess,sys,time\n"
         "g=subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)'])\n"
         "open(sys.argv[1],'w').write(f'{os.getpid()} {g.pid}')\ntime.sleep(300)")
SELF = f"import sys\nsys.path.insert(0,sys.argv[2])\nimport job_object\njob_object.ensure_self_job()\n{SPAWN}"


def until(cond, limit=15):
    end = time.monotonic() + limit
    while time.monotonic() < end and not cond():
        time.sleep(0.1)
    return cond()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp, self.pids = Path(tempfile.mkdtemp()), []
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.addCleanup(lambda: [os.kill(p, 9) for p in self.pids if scheduler.pid_alive(p)])

    def assertDead(self, path):
        self.assertTrue(until(lambda: path.exists() and len(path.read_text().split()) == 2, 20), "pid が書かれなかった")
        pids = [int(x) for x in path.read_text().split()]
        self.pids += pids
        self.assertTrue(until(lambda: not any(scheduler.pid_alive(p) for p in pids)), f"生き残り: {pids}")


@unittest.skipUnless(sys.platform == "win32", "Windows の Job Object が要る")
class JobKillsTheTree(Base):
    def test_g1_ttl_kills_child_and_grandchild(self):
        f, g = self.tmp / "a", self.tmp / "b"
        rc, _, err = proc.run([sys.executable, "-c", SPAWN, str(f)], self.tmp, 5, "spawn")
        self.assertEqual((rc, err), (124, "TTL超過 (5s): spawn"))
        self.assertDead(f)
        self.assertEqual(scheduler.run_logged([sys.executable, "-c", SPAWN, str(g)], self.tmp, 5, self.tmp / "x", None), 124)
        self.assertIn("TTL超過", (self.tmp / "x").read_text(encoding="utf-8"))
        self.assertDead(g)

    def test_g2_killing_the_harness_takes_its_descendants(self):
        f = self.tmp / "pids"
        p = subprocess.Popen([sys.executable, "-c", SELF, str(f), str(HARNESS)])
        self.addCleanup(p.kill)
        self.assertTrue(until(lambda: f.exists() and f.read_text().count(" "), 20), "孫が起きなかった")
        p.kill()
        p.wait(timeout=30)
        self.assertDead(f)


class Behaviour(Base):
    def test_g3_run_keeps_returncode_streams_and_stdin(self):
        run = lambda code, **kw: proc.run([sys.executable, "-c", code], self.tmp, 30, "t", **kw)  # noqa: E731
        self.assertEqual(run("import sys;print('o');print('e',file=sys.stderr);sys.exit(3)"), (3, "o\n", "e\n"))
        self.assertEqual(run("import sys;print(sys.stdin.read().upper(),end='')", input="abc"), (0, "ABC", ""))
        rc, out, err = proc.run(["no-such-command-xyz"], self.tmp, 5, "t")
        self.assertEqual((rc, out), (127, ""))
        self.assertTrue(err.startswith("コマンドが見つかりません"))
        log = self.tmp / "x"
        self.assertEqual(scheduler.run_logged([sys.executable, "-c", "print('hi')"], self.tmp, 30, log, None), 0)
        self.assertEqual(scheduler.run_logged(["no-such-command-xyz"], self.tmp, 5, log, None), 127)

    def test_g3_job_api_never_raises_and_normalized_arms_it(self):
        self.assertEqual(job_object.ensure_self_job(), job_object.ensure_self_job())
        job_object.close(None)
        self.assertFalse(job_object.assign(None, os.getpid()))
        if sys.platform != "win32":
            self.assertEqual((job_object.supported(), job_object.open_job()), (False, None))
        with mock.patch.object(job_object, "ensure_self_job", return_value=False) as m:
            self.assertEqual(exitcode.normalized(lambda: 5), 5)
        m.assert_called_once_with()


def harness_trees():
    for p in sorted(HARNESS.rglob("*.py")):
        if "templates" not in p.relative_to(HARNESS).parts:
            yield p.relative_to(HARNESS).as_posix(), ast.parse(p.read_text(encoding="utf-8"))


class StaticAudit(unittest.TestCase):
    def test_g4_every_spawning_entry_point_goes_through_normalized(self):
        bad = []
        for rel, tree in harness_trees():
            mods = {(getattr(n, "module", None) or a.name).split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
            main = [ast.unparse(n) for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'"]
            if main and mods & {"subprocess", "proc", "scheduler"} and not any("exitcode.normalized(" in m for m in main):
                bad.append(rel)
        self.assertEqual(bad, [])

    def test_g4_normalized_calls_ensure_self_job_and_nothing_breaks_away(self):
        trees = dict(harness_trees())
        fn = next(n for n in ast.walk(trees["exitcode.py"]) if isinstance(n, ast.FunctionDef) and n.name == "normalized")
        self.assertIn("job_object.ensure_self_job()", ast.unparse(fn))
        bad = [rel for rel, tree in trees.items() for n in ast.walk(tree) if "CREATE_BREAKAWAY_FROM_JOB" in (
            getattr(n, "attr", None), getattr(n, "id", None), getattr(n, "value", None))]
        self.assertEqual(bad, [])

    def test_g5_this_file_launches_no_git_gh_or_agent(self):
        strings = {n.value for n in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))) if isinstance(n, ast.Constant)}
        self.assertEqual(strings & set("git gh claude agy gemini codex".split()), set())


if __name__ == "__main__":
    unittest.main()
