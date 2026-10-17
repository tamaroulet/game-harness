"""工廠の走行の記録（W-95）の試験。セッションの記録が 1 件も無い走行で、生ログと queue.json から
calls/・tokens・env.json・reason が欠けなく取れること。CLI・アカウント・非公開オラクルは使わず、子プロセス（proc.run）を偽物に差し替える。"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402

SPEC = "# B1\n\nリポジトリの根の `calc` パッケージを作る。\n"
MODEL = "claude-sonnet-5-5"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
KEYS = ("input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens")
CLI_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
PLANS = [(100, 200, 300, 400), (10, 20, 30, 40), (1, 2, 3, 4)]
IMPLS = [(192_000, 500, 400_000, 943), (5000, 60, 70000, 80)]
REVIEW = (7, 8, 9, 10)
ENV_KEYS = {"os", "cli_version", "python", "mujoco", "base", "start", "account", "started_at", "finished_at", "quota_wait_seconds"}
CP932 = "import sys; sys.stdout.buffer.write('漏れの跡があります\\n'.encode('cp932')); sys.stderr.buffer.write('標準エラー\\n'.encode('cp932')); sys.exit(1)"
REAL_RUN = bench_run.proc.run


def sh(repo, *args):
    r = subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def cli_json(usage, model=MODEL, sid="sid"):
    """本物の CLI（claude -p --output-format json）が標準出力に書く 1 行の形。"""
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "result": "答え",
                       "session_id": sid, "usage": dict(zip(CLI_KEYS, usage)),
                       "modelUsage": {model: {"inputTokens": usage[0], "outputTokens": usage[3]}}}, ensure_ascii=False)


def write_log(path, usage, prompt, model=MODEL, sid="sid"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cli_json(usage, model, sid) + "\n--- stderr ---\n", encoding="utf-8")
    path.with_name(path.stem + ".prompt.txt").write_text(prompt, encoding="utf-8")


def tries_usage(usage):
    return {**dict(zip(KEYS, usage)), "total_tokens": sum(usage)}


class RecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out, self.sessions = self.root / "clone", self.root / "out", self.root / "sessions"
        self.sessions.mkdir()
        self.work = self.out / "work" / "B1" / "factory-r1"
        self.guard_script = None

    # ---- 偽物
    def fake_plan(self, args):
        inbox = Path(args[args.index("--inbox") + 1])
        inbox.mkdir(parents=True, exist_ok=True)
        for name in ("P-01.md", "ledger.yaml"):
            (inbox / name).write_text(f"タスク: {name}\n", encoding="utf-8")
        (inbox / "planner.json").write_text(json.dumps({"files": {}, "problems": [], "loop": {"rounds": len(self.plans)}}), encoding="utf-8")
        log = self.work / "out" / "planner"
        for n, usage in enumerate(self.plans, 1):
            write_log(log / f"calc-{n}.log", usage, f"計画の入力 {n}", sid=f"plan{n}")
        if self.plans:
            (log / "calc.log").write_text((log / f"calc-{len(self.plans)}.log").read_text(encoding="utf-8"), encoding="utf-8")
        return 0, "", ""

    def fake_poll(self, cwd, args):
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
        out = Path(cfg["out"])
        tries = []
        for n, usage in enumerate(self.impls, 1):
            write_log(out / "P-01" / f"implementer-1-{n}.log", usage, f"実装の入力 {n}", model=self.impl_model, sid=f"impl{n}")
            tries.append({"run": 1, "attempt": n, "usage": tries_usage(usage)})
        if self.reviews:
            write_log(out / "review" / "abc123.log", self.reviews, "監修の入力", model="claude-haiku-5-5", sid="rev")
        (out / "queue").mkdir(parents=True, exist_ok=True)
        (out / "queue" / "P-01.md").write_text("What P-01\n", encoding="utf-8")
        item = {"status": "waiting" if self.limit else "done","deps": [], "replaced": False, "tries": tries}
        (out / "queue.json").write_text(json.dumps({"items": {"P-01": item}, "skipped": [], "quota_wait": None}), encoding="utf-8")
        return 0, "", ""

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if args[-1] == "--version":
            return 0, "2.1.5 (Claude Code)\n", ""
        module = args[args.index("-m") + 1].removeprefix("harness.")
        if module == "bench_guard":
            if args[args.index("-m") + 2] == "check" and self.guard_script:
                return REAL_RUN([sys.executable, "-c", self.guard_script], cwd, ttl, label, env=env)
            return 0, "", ""
        if module == "plan_loop":
            return self.fake_plan(args)
        if module == "bench_start":
            tree = sh(self.repo, "rev-parse", "HEAD^{tree}")
            self.start = sh(self.repo, "commit-tree", tree, "-m", "start")
            return 0, self.start + "\n", ""
        if module == "hline":
            code = self.fake_poll(cwd, args)
            sh(cwd, "push", "-q", "origin", f"HEAD:refs/heads/bench/B1/factory-r1")
            return code
        raise AssertionError(f"想定外の子プロセス: {args}")

    def build_repo(self):
        self.repo.mkdir()
        sh(self.repo, "init", "-q")
        files = {"docs/bench/B1/spec.md": SPEC, "calc/__init__.py": "x = 1\n",
                 "config/hline.json": json.dumps({"implementer": {"cli": "claude", "model": MODEL}, "oracles": "O"})}
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "base")
        return sh(self.repo, "rev-parse", "HEAD")

    def invoke(self, argv):
        with contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(bench_run.proc, "run", self.fake_run), \
                mock.patch.object(bench_run.proc, "resolve_cli", lambda name: ["claude"]), \
                mock.patch.object(bench_run, "version_problem", lambda repo, rev: None), \
                mock.patch.object(bench_run, "sleep", lambda s: None):
            return bench_run.main(argv)

    def go(self, plans=PLANS, impls=IMPLS, reviews=REVIEW, impl_model=MODEL, limit=None):
        self.plans, self.impls, self.reviews, self.impl_model, self.limit = plans, impls, reviews, impl_model, limit
        base = self.build_repo()
        argv = ["factory", "--task", "B1", "--run-id", "r1", "--repo", str(self.repo), "--out", str(self.out),
                "--base", base, "--sessions", str(self.sessions)]
        if limit is None:
            return self.invoke(argv)
        with mock.patch.object(bench_run, "TOKEN_LIMIT", limit):
            return self.invoke(argv)

    def finish(self):
        def fake_score(task, tree, out, oracles=None):
            Path(out).write_text(json.dumps({"passed": 1, "total": 2, "timed_out": False}), encoding="utf-8")
            return 0
        with mock.patch.object(bench_run, "score_tree", fake_score):
            rc = self.invoke(["finish", "--task", "B1", "--run-id", "r1", "--repo", str(self.repo), "--out", str(self.out)])
        self.assertEqual(rc, 0)
        return json.loads((self.repo / "bench" / "records" / "B1" / "factory-r1.json").read_text(encoding="utf-8"))

    @property
    def rundir(self):
        return self.out / "runs" / "B1" / "factory-r1"

    # ---- 合格条件 2
    def test_calls_and_tokens_without_sessions(self):
        self.assertEqual(self.go(), 0)
        calls = sorted((self.rundir / "calls").iterdir())
        self.assertEqual(len(calls), len(PLANS) + len(IMPLS) + 1)
        self.assertEqual(sorted(p.name.split("-", 1)[1] for p in calls),
                         ["implementer"] * 2 + ["planner"] * 3 + ["reviewer"])
        for call in calls:
            self.assertTrue((call / "result.json").is_file())
            self.assertTrue((call / "prompt.txt").is_file())
            self.assertIn("入力", (call / "prompt.txt").read_text(encoding="utf-8"))
        everything = PLANS + IMPLS + [REVIEW]
        sums = [sum(u[i] for u in everything) for i in range(4)]
        record = self.finish()
        self.assertEqual(record["tokens"], {**dict(zip(KEYS, sums)), "total": sum(sums)})
        queue = json.loads((self.rundir / "factory" / "queue.json").read_text(encoding="utf-8"))
        queue_sum = sum(sum(t["usage"][k] for k in KEYS) for t in queue["items"]["P-01"]["tries"])
        impl_sum = sum(sum(u) for u in IMPLS)
        self.assertEqual(queue_sum, impl_sum)
        impl_total = 0
        for call in calls:
            if call.name.endswith("implementer"):
                doc = json.loads((call / "result.json").read_text(encoding="utf-8"))
                impl_total += sum(bench_run.usage_of(doc))
        self.assertEqual(impl_total, queue_sum)
        self.assertEqual(record["invalid"], False)

    def test_token_limit_stops(self):
        self.assertEqual(self.go(limit=1000), 0)
        run = json.loads((self.rundir / "factory" / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(run["stop"], "tokens")

    # ---- 合格条件 3
    def test_forbidden_model_is_req02(self):
        self.assertEqual(self.go(impl_model="claude-opus-4-1"), 0)
        record = self.finish()
        self.assertEqual(record["invalid"], "REQ-02")

    def test_no_calls_is_req22(self):
        self.assertEqual(self.go(plans=[], impls=[], reviews=None), 0)
        self.assertEqual(list((self.rundir / "calls").glob("*/result.json")), [])
        record = self.finish()
        self.assertEqual((record["invalid"], record["reason"]), ("REQ-22", "呼び出しの記録がありません"))

    # ---- 合格条件 4
    def test_env_json_fields(self):
        self.assertEqual(self.go(), 0)
        env = json.loads((self.rundir / "env.json").read_text(encoding="utf-8"))
        self.assertTrue(ENV_KEYS <= env.keys(), ENV_KEYS - env.keys())
        self.assertEqual(env["cli_version"], "2.1.5 (Claude Code)")
        self.assertEqual(env["base"], sh(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(env["start"], self.start)
        self.assertTrue(env["account"])
        self.assertEqual(env["quota_wait_seconds"], 0)

    def test_cp932_child_output_is_readable_in_reason(self):
        self.guard_script = CP932
        self.assertEqual(self.go(plans=[], impls=[], reviews=None), 0)
        record = self.finish()
        self.assertEqual(record["invalid"], "REQ-10")
        self.assertIn("漏れの跡があります", record["reason"])
        self.assertIn("標準エラー", record["reason"])


if __name__ == "__main__":
    unittest.main()
