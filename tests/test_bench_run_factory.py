"""工廠の走らせ役（W-46）の試験。計画役・監修役・実装役の CLI、非公開オラクル、本物のアカウントは使わず、
子プロセス（proc.run）を偽物に差し替える。git は一時のディレクトリのリポジトリだけ。"""
import contextlib
import datetime
import hashlib
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

T0 = 1_800_000_000   # 60 の倍数（quota_wait の時刻は分までしか持たない）
SPEC = "# B1\n\nリポジトリの根の `calc` パッケージを作る。\n"
MODEL = "claude-sonnet-5-5"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
KEYS = ("task", "run_id", "base", "start", "head", "started_at", "invalid", "reason", "violations", "passed", "total",
        "rate", "timed_out", "stop", "plan_rounds", "plan_confirmed", "plan_frozen", "planner_sha256", "queue_sha256",
        "replaced", "tries", "first_pass", "recovered", "intake_revised", "tokens", "quota_wait_seconds", "manifest_sha256")
RUN_KEYS = set(KEYS) - {"violations", "passed", "total", "rate", "timed_out", "manifest_sha256"}
ALL_WAITING = {"P-01": ("waiting", [], False, 0)}
ALL_DONE = {"P-01": ("done", [], False, 1)}
HAPPY = [
    dict(commit="calc/m1.py", advance=100, impl=[(10, 20, 30, 40)], review=[(1, 2, 3, 4)],
         items={"P-01": ("done", [], False, 2), "P-02": ("waiting", ["P-01"], True, 0), "P-03": ("waiting", ["P-02"], False, 0)},
         skipped=[{"file": "P-04.md", "reason": "監修役が差し戻しました: 大きすぎる"},
                  {"file": "P-06.md", "reason": "マイルストーン W-1 はこのラインに許されていません"}]),
    dict(commit="calc/m2.py", advance=100, impl=[(5, 6, 7, 8)], review=[(1, 1, 1, 1)],
         items={"P-01": ("done", [], False, 2), "P-02": ("done", ["P-01"], True, 1), "P-03": ("waiting", ["P-02"], False, 0)},
         skipped=[{"file": "P-04.md", "reason": "監修役が差し戻しました: 大きすぎる"}]),
    dict(commit="tests/test_calc_m.py", advance=100, impl=[(2, 2, 2, 2)],
         items={"P-01": ("done", [], False, 2), "P-02": ("done", ["P-01"], True, 1), "P-03": ("done", ["P-02"], False, 1)},
         skipped=[{"file": "P-05.md", "reason": "監修役が差し戻しました: 曖昧"}]),
]


def sh(repo, *args):
    r = subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", check=True)
    return r.stdout.strip()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def iso(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="minutes")


class Clock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        if seconds != bench_run.PAUSE:   # 進みの無い poll の後の小休止では時計を進めない
            self.t += seconds


class FactoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out, self.sessions = self.root / "clone", self.root / "out", self.root / "sessions"
        self.sessions.mkdir()
        self.clock = Clock()
        self.n = 0
        self.made, self.heads, self.cfgs, self.ttls, self.children = [], [], [], [], []
        self.start = self.tip = None

    # ---- 偽物
    def session(self, cwd, usage, model=MODEL, name=None):
        self.n += 1
        sid = f"s{self.n:03d}"
        ts = f"2026-10-16T10:{self.n // 60:02d}:{self.n % 60:02d}Z"
        a, b, c, d = usage
        base = {"sessionId": sid, "cwd": str(cwd), "timestamp": ts}
        msg = {"id": f"m{self.n}", "model": model, "role": "assistant", "content": [{"type": "text", "text": "答え"}]}
        lines = [{**base, "type": "user", "message": {"role": "user", "content": f"入力の全文 {sid}"}},
                 {**base, "type": "assistant", "message": {**msg, "usage": {"input_tokens": a, "cache_creation_input_tokens": b,
                                                                           "cache_read_input_tokens": c, "output_tokens": 1}}},
                 {**base, "type": "assistant", "message": {**msg, "usage": {"input_tokens": a, "cache_creation_input_tokens": b,
                                                                           "cache_read_input_tokens": c, "output_tokens": d}}}]
        (self.sessions / "proj").mkdir(exist_ok=True)
        (self.sessions / "proj" / f"{name or sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
        self.made.append(usage)
        return sid

    def fake_plan(self, args):
        inbox = Path(args[args.index("--inbox") + 1])
        self.cfgs.append(json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8")))
        inbox.mkdir(parents=True, exist_ok=True)
        files = {}
        for name in ("P-01.md", "P-02.md", "P-03.md", "ledger.yaml"):
            (inbox / name).write_text(f"タスク: {name}\n", encoding="utf-8")
            files[name] = sha256(inbox / name)
        loop = {"rounds": self.plan_rounds, "confirmed": self.plan_code == 0, "failed": self.failed}
        (inbox / "planner.json").write_text(json.dumps({"files": files, "problems": [], "loop": loop}), encoding="utf-8")
        for usage in self.plan_usage:
            self.session(inbox, usage)
        self.clock.t += self.plan_advance
        return self.plan_code, "", ""

    def fake_start(self):
        tree = sh(self.repo, "rev-parse", "HEAD^{tree}")
        self.start = self.tip = sh(self.repo, "commit-tree", tree, "-m", "start")
        return 0, self.start + "\n", ""

    def fake_poll(self, cwd, ttl, args):
        cfg = json.loads(Path(args[args.index("--config") + 1]).read_text(encoding="utf-8"))
        self.cfgs.append(cfg)
        self.ttls.append(ttl)
        self.heads.append((sh(cwd, "rev-parse", "HEAD"), self.tip))
        spec = self.polls(len(self.ttls))
        if spec.get("commit"):
            path = Path(cwd) / spec["commit"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{len(self.ttls)}\n", encoding="utf-8")
            sh(cwd, "add", "-A")
            sh(cwd, "commit", "-q", "-m", f"c{len(self.ttls)}")
            sh(cwd, "push", "-q", "origin", f"HEAD:refs/heads/{cfg['integration_branch']}")
            self.tip = sh(cwd, "rev-parse", "HEAD")
        self.clock.t += spec.get("advance", 0)
        for usage in spec.get("impl", ()):
            self.session(Path(cfg["worktrees"]) / "hline-p01-ab12", usage, spec.get("impl_model", MODEL))
        for usage in spec.get("review", ()):
            self.session(cwd, usage, "claude-haiku-5-5")
        items = {n: {"status": s, "deps": d, "replaced": r, "tries": [{}] * t} for n, (s, d, r, t) in spec.get("items", {}).items()}
        out = Path(cfg["out"])
        (out / "queue").mkdir(parents=True, exist_ok=True)
        for n in items:
            (out / "queue" / f"{n}.md").write_text(f"What {n}\n", encoding="utf-8")
        (out / "queue.json").write_text(json.dumps({"items": items, "skipped": spec.get("skipped", []),
                                                    "quota_wait": spec.get("quota")}), encoding="utf-8")
        return spec.get("code", 0), "", spec.get("err", "")

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if args[-1] == "--version":
            return 0, self.cli_version + "\n", ""
        module = args[args.index("-m") + 1].removeprefix("harness.")
        self.children.append((module, args[args.index("-m") + 2] if module in ("bench_guard", "hline") else None))
        if module == "bench_guard":
            code = self.guard_code if args[args.index("-m") + 2] == "check" else self.scan_code
            return code, "漏れ\n" if code else "", ""
        if module == "plan_loop":
            return self.fake_plan(args)
        if module == "bench_start":
            return self.fake_start()
        if module == "hline":
            return self.fake_poll(cwd, ttl, args)
        raise AssertionError(f"想定外の子プロセス: {args}")

    # ---- 走らせる
    def build_repo(self, freeze=None):
        self.repo.mkdir()
        sh(self.repo, "init", "-q")
        files = {"docs/bench/B1/spec.md": SPEC, "calc/__init__.py": "x = 1\n", "harness/tool.py": "t = 1\n",
                 "config/hline.json": json.dumps({"implementer": {"cli": "claude", "model": MODEL}, "oracles": "O"})}
        if freeze is not None:
            files["bench/records/freeze.json"] = json.dumps({"cli_version": freeze})
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "base")
        return sh(self.repo, "rev-parse", "HEAD")

    def go(self, polls=HAPPY, plan_code=0, guard_code=0, freeze=None, cli_version="2.0.0 (Claude Code)", plan_rounds=2,
           failed=(), plan_advance=0, plan_usage=((100, 200, 300, 400), (1, 2, 3, 4)), scan_code=0):
        self.polls = polls if callable(polls) else (lambda n: polls[min(n, len(polls)) - 1])
        self.plan_code, self.guard_code, self.cli_version, self.scan_code = plan_code, guard_code, cli_version, scan_code
        self.plan_rounds, self.failed, self.plan_advance, self.plan_usage = plan_rounds, list(failed), plan_advance, plan_usage
        self.base = self.build_repo(freeze)
        self.session(self.root / "elsewhere", (9, 9, 9, 9), name="before")   # 走行の前からあるセッションは数えない
        self.made.clear()
        return self.factory_rc()

    def argv(self, cmd, extra=()):
        return [cmd, "--task", "B1", "--run-id", "r1", "--repo", str(self.repo), "--out", str(self.out), *extra]

    def invoke(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(bench_run.proc, "run", self.fake_run), \
                mock.patch.object(bench_run.proc, "resolve_cli", lambda name: ["claude"]), \
                mock.patch.object(bench_run, "clock", self.clock), mock.patch.object(bench_run, "sleep", self.clock.sleep):
            return bench_run.main(argv)

    def factory_rc(self):
        return self.invoke(self.argv("factory", ["--base", self.base, "--sessions", str(self.sessions)]))

    def finish(self, score=(3, 4, False)):
        def fake_score(task, tree, out, oracles=None):
            self.scored = sorted(p.relative_to(tree).as_posix() for p in Path(tree).rglob("*") if p.is_file() and ".git" not in p.parts)
            Path(out).write_text(json.dumps({"passed": score[0], "total": score[1], "timed_out": score[2]}), encoding="utf-8")
            return 0
        with mock.patch.object(bench_run, "score_tree", fake_score):
            rc = self.invoke(self.argv("finish"))
        self.assertEqual(rc, 0)
        return json.loads((self.repo / "bench" / "records" / "B1" / "factory-r1.json").read_text(encoding="utf-8"))

    @property
    def rundir(self):
        return self.out / "runs" / "B1" / "factory-r1"

    def run_json(self):
        return json.loads((self.rundir / "factory" / "run.json").read_text(encoding="utf-8"))

    # ---- 合格条件 2
    def test_full_run_and_record(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.heads), 3)
        for head, tip in self.heads:
            self.assertEqual(head, tip)   # どの poll も、起こす時点で HEAD が統合ブランチの先端（最初は開始のコミット）
        self.assertEqual(self.heads[0][0], self.start)
        self.assertNotEqual(self.heads[1][0], self.start)
        cfg = self.cfgs[-1]   # 最後の設定は H ライン用
        work = self.out / "work" / "B1" / "factory-r1"
        self.assertEqual({k: cfg[k] for k in ("inbox", "out", "worktrees", "base", "integration_branch", "milestones", "open_pr",
                                              "gate_always_modules")},
                         {"inbox": str(work / "inbox"), "out": str(work / "out"), "worktrees": str(work / "wt"),
                          "base": self.start, "integration_branch": "bench/B1/factory-r1", "milestones": ["bench"],
                          "open_pr": False, "gate_always_modules": []})
        self.assertEqual(cfg["oracles"], "O")   # ほかの欄は clone の設定のまま
        for name in ("env.json", "inputs", "git", "calls", "factory/run.json", "git/run.bundle", "inputs/spec.md", "inputs/P-01.md"):
            self.assertTrue((self.rundir / name).exists(), name)
        calls = sorted(p for p in (self.rundir / "calls").iterdir())
        self.assertEqual(len(calls), len(self.made))
        self.assertEqual(sorted(p.name.split("-", 1)[1] for p in calls),
                         ["implementer"] * 3 + ["planner"] * 2 + ["reviewer"] * 2)
        for call in calls:
            self.assertEqual({p.name for p in call.iterdir()}, {"prompt.txt", "result.json", "session.jsonl"})
            self.assertIn("入力の全文", (call / "prompt.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.run_json().keys(), RUN_KEYS)
        self.assertEqual(self.run_json()["stop"], "done")

        record = self.finish()
        self.assertEqual(list(record), list(KEYS))
        sums = [sum(u[i] for u in self.made) for i in range(4)]
        self.assertEqual(record["tokens"], {"input_tokens": sums[0], "cache_creation_tokens": sums[1],
                                            "cache_read_tokens": sums[2], "output_tokens": sums[3], "total": sum(sums)})
        self.assertEqual((record["replaced"], record["tries"], record["plan_rounds"], record["intake_revised"]), (1, 4, 2, 2))
        self.assertEqual(record["plan_confirmed"], True)
        self.assertEqual(record["invalid"], False)
        self.assertEqual((record["passed"], record["total"], record["rate"], record["violations"]), (3, 4, 0.75, []))
        self.assertEqual(sorted(record["planner_sha256"]), ["P-01.md", "P-02.md", "P-03.md", "ledger.yaml"])
        self.assertEqual(sorted(record["queue_sha256"]), ["P-01", "P-02", "P-03"])
        self.assertEqual(record["queue_sha256"]["P-02"], sha256(work / "out" / "queue" / "P-02.md"))
        self.assertEqual(record["start"], self.start)
        self.assertEqual(record["head"], self.tip)
        self.assertEqual(record["base"], self.base)
        self.assertEqual(record["manifest_sha256"], sha256(self.rundir / "manifest.json"))
        manifest = json.loads((self.rundir / "manifest.json").read_text(encoding="utf-8"))
        self.assertIn("factory/run.json", manifest)
        self.assertIn("guard.txt", manifest)
        self.assertNotIn("manifest.json", manifest)
        self.assertIn("calc/m2.py", self.scored)   # 先端の木を採点した

    def test_head_must_be_rev(self):
        self.build_repo()
        self.repo.joinpath("more.txt").write_text("x", encoding="utf-8")
        sh(self.repo, "add", "-A")
        sh(self.repo, "commit", "-q", "-m", "second")
        old = sh(self.repo, "rev-parse", "HEAD~1")
        self.assertEqual(self.invoke(self.argv("factory", ["--base", old, "--sessions", str(self.sessions)])), 2)
        self.assertFalse(self.out.exists())

    # ---- 合格条件 3
    def test_invalid_runs_copy_reason_to_record(self):
        cases = [("REQ-10", "bench_guard check", dict(guard_code=1)), ("REQ-03", "CLI の版", dict(freeze="1.0.0")),
                 ("plan", "plan_loop", dict(plan_code=2))]
        for want, text, kw in cases:
            with self.subTest(want=want):
                self.setUp()
                self.assertEqual(self.go(**kw), 0)
                self.assertEqual(self.run_json()["invalid"], want)
                self.assertEqual(self.ttls, [])   # H ラインは回さない
                record = self.finish()
                self.assertEqual((record["invalid"], record["passed"], record["total"]), (want, 0, 0))
                self.assertIn(text, record["reason"])

    def test_matching_cli_version_is_valid(self):
        self.assertEqual(self.go(freeze="2.0.0"), 0)
        self.assertIsNone(self.run_json()["invalid"])

    def test_scan_hit_is_req_11_and_foreign_model_is_req_02(self):
        self.assertEqual(self.go(scan_code=1), 0)
        record = self.finish()
        self.assertEqual((record["invalid"], record["passed"]), ("REQ-11", 0))
        self.assertIn("漏れ", (self.rundir / "guard.txt").read_text(encoding="utf-8"))
        self.setUp()
        polls = [{**HAPPY[0], "impl_model": "claude-opus-5-5"}, HAPPY[2]]
        self.assertEqual(self.go(polls=polls), 0)
        record = self.finish()
        self.assertEqual((record["invalid"], record["passed"]), ("REQ-02", 0))

    def test_unconfirmed_plan_is_a_valid_zero_run(self):
        failed = [{"stage": "review", "target": "P-01", "reason": "監修役が差し戻しました"}]
        self.assertEqual(self.go(plan_code=1, failed=failed, plan_rounds=3), 0)
        self.assertEqual(self.ttls, [])
        run = self.run_json()
        self.assertIsNone(run["invalid"])
        self.assertEqual(run["reason"], failed)
        record = self.finish()
        self.assertEqual((record["invalid"], record["passed"], record["plan_confirmed"], record["plan_rounds"]),
                         (False, 0, False, 3))
        self.assertEqual(record["reason"], failed)

    def test_change_outside_package_is_a_violation(self):
        polls = [{**HAPPY[0], "commit": "harness/tool.py"}, HAPPY[2]]
        self.assertEqual(self.go(polls=polls), 0)
        record = self.finish()
        self.assertEqual(record["violations"], ["harness/tool.py"])
        self.assertEqual((record["passed"], record["invalid"]), (0, False))

    def test_finish_without_run_json_exits_1(self):
        self.build_repo()
        self.assertEqual(self.invoke(self.argv("finish")), 1)

    # ---- 合格条件 4
    def test_poll_ttl_and_ttl_cut_streak(self):
        cut = dict(code=124, err="TTL超過 (1800s): hline poll", advance=0, items=ALL_WAITING)
        polls = [{**cut, "advance": 1000}, {**cut, "advance": 500}, cut]
        self.assertEqual(self.go(polls=polls, plan_advance=9000), 0)
        self.assertEqual(self.ttls, [1800, 800, 300])   # 3 時間の残りと 3,600 秒の小さいほう
        run = self.run_json()
        self.assertEqual(run["invalid"], "REQ-22")
        self.assertIn("124", run["reason"])
        self.assertIn("TTL", run["reason"])
        self.assertEqual(self.finish()["invalid"], "REQ-22")

    def test_first_ttl_is_capped_at_3600(self):
        self.assertEqual(self.go(polls=[dict(items=ALL_DONE)]), 0)
        self.assertEqual(self.ttls, [3600])

    def test_same_ending_three_times_stops_and_mixed_continues(self):
        bad = dict(code=1, err="x\nboom\n\n", items=ALL_WAITING)
        self.assertEqual(self.go(polls=[bad, bad, bad]), 0)
        self.assertEqual((len(self.ttls), self.run_json()["invalid"]), (3, "REQ-22"))
        self.setUp()
        silent = dict(code=3, err="", items=ALL_WAITING)
        self.assertEqual(self.go(polls=[silent, silent, silent]), 0)
        self.assertEqual((len(self.ttls), self.run_json()["invalid"]), (3, "REQ-22"))
        self.setUp()
        ok = dict(items=ALL_WAITING)
        other = dict(code=1, err="other", items=ALL_WAITING)
        polls = [bad, bad, ok, bad, bad, other, bad, bad, dict(items=ALL_DONE)]
        self.assertEqual(self.go(polls=polls), 0)
        self.assertEqual(len(self.ttls), 9)
        run = self.run_json()
        self.assertEqual((run["invalid"], run["stop"]), (None, "done"))

    def test_three_hours_stops_as_valid_run(self):
        slow = dict(advance=3600, items=ALL_WAITING)
        self.assertEqual(self.go(polls=[slow], plan_advance=0), 0)
        self.assertEqual(len(self.ttls), 3)
        run = self.run_json()
        self.assertEqual((run["invalid"], run["stop"], run["quota_wait_seconds"]), (None, "time", 0))
        self.assertEqual(self.finish()["stop"], "time")

    def test_planning_counts_toward_three_hours(self):
        self.assertEqual(self.go(polls=[dict(advance=3600, items=ALL_WAITING)], plan_advance=7200), 0)
        self.assertEqual(len(self.ttls), 1)
        self.assertEqual(self.run_json()["stop"], "time")

    def test_quota_wait_is_not_counted(self):
        def polls(n):
            if n == 1:
                return dict(advance=3600, items=ALL_WAITING, quota={"name": "P-01", "reason": "r", "at": iso(T0), "until": iso(T0 + 7200)})
            if self.clock.t < T0 + 7200:   # 回復待ちの間の poll は何もせず終わる
                return dict(items=ALL_WAITING, quota={"name": "P-01", "reason": "r", "at": iso(T0), "until": iso(T0 + 7200)})
            return dict(advance=3600, items=ALL_WAITING)
        self.assertEqual(self.go(polls=polls), 0)
        run = self.run_json()
        self.assertEqual((run["invalid"], run["stop"], run["quota_wait_seconds"]), (None, "time", 7200))
        self.assertEqual(self.clock.t, T0 + 18000)   # 3 時間 + 回復待ちの 2 時間

    def test_eight_million_tokens_stops(self):
        big = dict(items=ALL_WAITING, impl=[(2_000_000, 2_000_000, 2_000_000, 2_000_000)])
        self.assertEqual(self.go(polls=[big], plan_usage=()), 0)
        run = self.run_json()
        self.assertEqual((len(self.ttls), run["invalid"], run["stop"]), (1, None, "tokens"))
        self.assertEqual(run["tokens"]["total"], 8_000_000)
        self.setUp()
        under = dict(items=ALL_WAITING, impl=[(2_000_000, 2_000_000, 2_000_000, 1_999_999)])
        self.assertEqual(self.go(polls=[under, dict(items=ALL_DONE)], plan_usage=()), 0)
        run = self.run_json()
        self.assertEqual((len(self.ttls), run["stop"]), (2, "done"))
        self.assertEqual(run["tokens"]["total"], 7_999_999)


if __name__ == "__main__":
    unittest.main()
