"""改善ループの見回り（harness/cycle.py・cycle_steps.py・cycle_base.py）の検査。段階を全部、偽のジョブと偽の命令で通す。

    python -m unittest tests.test_cycle -v

git は一時のリポジトリで本物を使う（push と PR は偽物）。タスク スケジューラ・モデル・GitHub は呼ばない。止まる所と、取り込まない所と、
途中で落ちて続ける所を見る。
"""
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
sys.path[:0] = [str(ROOT / "harness")]
import bench_job  # noqa: E402
import cycle  # noqa: E402
import cycle_base as base  # noqa: E402
import cycle_steps as steps  # noqa: E402

DEV_BASES, HELD_BASES = ("B07", "B08", "B09"), ("B05", "B06", "B10")
PROMPT = 'def build_prompt(what, feedback=None, symbol_map=None):\n    return "古い指示"\n\n\ndef ledger_section(wt, what):\n    return ""\n'
PROPOSAL = '## 狙う失敗の型\n型\n## 出た段\n- B08 s4\n- B09 s6\n## 直し\n直し\n## 差分\n```json\n{"max_attempts": 3}\n```\n## 予想\nx\n## 補う弱さ\nx\n## 外す条件\nx\n'
ONE_STAGE = PROPOSAL.replace("- B09 s6\n", "")


def row(k, ok=True, tokens=1_000_000):
    return {"k": k, "new": [2, 2] if ok else [1, 2], "kept": [3, 3], "invalid": False, "tokens": tokens}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args], check=True,
                          capture_output=True, text=True, encoding="utf-8", timeout=60).stdout


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.local = self.tmp / "local"
        self.local.mkdir()
        self.env({"src": str(self.tmp), "local": str(self.local), "agy_bin": str(self.tmp), "unity_editors": str(self.tmp)})
        self.cycle_home, self.run_dir = base.home(), base.runner()
        self.make_runner()
        self.make_places()
        self.patch(cycle, "ROOT", self.run_dir)
        self.commands, self.notes, self.prs, self.jobs, self.stopped = [], [], {}, {}, 0
        self.surface_code, self.suite_code, self.argon = 0, 0, "LOW"
        self.dev_ok, self.held_ok, self.dev_tokens = 16, 14, 1_000_000
        self.pair = {"old": False, "new": True}
        self.proposal_text = PROPOSAL
        self.patch(base.proc, "run", side_effect=self.fake_run)
        self.patch(base.bench_job, "status", side_effect=self.job_status)
        self.patch(base.bench_job, "submit", side_effect=self.job_submit)
        self.patch(base.bench_job, "stop", side_effect=self.job_stop)
        self.patch(base.bench_job, "task_state", return_value=("Ready", "0"))
        self.prepared = []
        self.patch(steps.ps, "prepare", side_effect=self.prepare)
        self.patch(steps.ps, "collect", side_effect=self.collect)
        self.patch(steps.ps, "discard", side_effect=lambda d: shutil.rmtree(d))
        self.out = self.local / "bench" / "repo-l6new" / ".bench"
        self.good_proposal = True

    def env(self, paths):
        path = self.tmp / "machine.json"
        path.write_text(json.dumps({"paths": paths}), encoding="utf-8")
        p = mock.patch.dict(os.environ, {"HARNESS_MACHINE": str(path)})
        p.start()
        self.addCleanup(p.stop)

    def patch(self, *args, **kw):
        p = mock.patch.object(*args, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def make_runner(self):
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True, timeout=60)
        run = self.run_dir
        (run / "harness").mkdir(parents=True)
        (run / "config").mkdir()
        git(run, "init", "-q", "-b", "main")
        git(run, "config", "core.autocrlf", "false")
        git(run, "remote", "add", "origin", str(origin))
        (run / "harness" / "hline_prompt.py").write_bytes(PROMPT.encode("utf-8"))
        (run / "config" / "surface.json").write_bytes(b'{\n  "max_attempts": 2\n}\n')
        (run / "config" / "cycle.json").write_bytes((ROOT / "config" / "cycle.json").read_bytes())
        self.base_rev_placeholder = None
        for tag, flap in (("l6n-dev", False), ("l6n-dev2", True)):
            for b in DEV_BASES:
                d = run / "bench" / "records" / "chains" / tag
                d.mkdir(parents=True, exist_ok=True)
                rows = [row(k, ok=not (b == "B08" and k == 4) and not (flap and b == "B09" and k == 6)) for k in range(1, 7)]
                (d / f"{b}.json").write_text(json.dumps({"tag": tag, "base": b, "rev": "HEADREV", "stages": rows}), encoding="utf-8")
        git(run, "add", "-A")
        git(run, "commit", "-q", "-m", "base")
        rev = git(run, "rev-parse", "HEAD").strip()
        for path in (run / "bench" / "records" / "chains").glob("*/*.json"):
            path.write_text(path.read_text(encoding="utf-8").replace("HEADREV", rev), encoding="utf-8")
        git(run, "add", "-A")
        git(run, "commit", "-q", "-m", "records")
        git(run, "push", "-q", "origin", "main")
        git(run, "branch", "-q", "--set-upstream-to=origin/main", "main")

    def make_places(self):
        chains = self.local / "oracles" / "chains"
        for b in (*DEV_BASES, *HELD_BASES):
            (chains / b / "s0" / "tree" / f"pkg{b.lower()}").mkdir(parents=True)
            hidden = chains / b / "s1" / "hidden" / f"test_chain_{b}_s1.py"
            hidden.parent.mkdir(parents=True)
            hidden.write_text("class HiddenThing:\n    def test_hidden_secret_name(self):\n        pass\n", encoding="utf-8")
        for tag in ("l6n-dev", "l6n-dev2"):
            s = self.local / "bench" / "chains" / f"{tag}-0123456789abcdef"
            s.mkdir(parents=True)
            (s / "chain.json").write_text(json.dumps({"tag": tag}), encoding="utf-8")
            (self.local / "bench" / "repo-l6new" / ".bench" / "chains" / tag).mkdir(parents=True)
        for tag, passed in (("held-v0", 12), ("l6n-held", 14)):
            self.write_held(tag, passed)

    def write_held(self, tag, passed):
        rows = [row(i + 1, ok=i < passed, tokens=2_000_000) for i in range(18)]
        for i, b in enumerate(HELD_BASES):
            d = self.local / "oracles" / "chains" / "runs" / tag / b
            d.mkdir(parents=True, exist_ok=True)
            (d / "public_full.json").write_text(json.dumps({"base": b, "stages": rows[i * 6:(i + 1) * 6]}), encoding="utf-8")

    # ---------------------------------------------------------- 偽のジョブと命令

    def job_submit(self, jid, cwd, cmds):
        self.jobs[jid] = {"cwd": str(cwd), "cmds": list(cmds), "state": "running"}

    def job_status(self, jid):
        job = self.jobs.get(jid)
        if job is None:
            return {"id": jid, "state": "none"}
        if job["state"] == "done":
            return {"id": jid, "state": "done", "exit": job.get("exit", 0)}
        return {"id": jid, "state": job["state"]}

    def job_stop(self):
        self.stopped += 1
        return 0

    def finish_jobs(self, exit_code=0):
        for job in self.jobs.values():
            if job["state"] == "running":
                job.update(state="done", exit=exit_code)

    def prepare(self, config, tags, now=None, root=None):
        d = self.local / "bench" / "proposer" / f"s{len(self.prepared)}"
        d.mkdir(parents=True)
        self.prepared.append((list(tags), d))
        return d

    def collect(self, config, dest):
        shutil.rmtree(dest)
        path = self.cycle_home / f"p{len(self.prepared)}.proposal.md"
        path.write_text(self.proposal_text, encoding="utf-8")
        rec = {"launched": True, "end": "正常", "path_hits": [], "unread_indexes": [], "shape_errors": [], "at": "t", "usage": None,
               "token_excess": False, "token_limit": 5000000, "proposal": str(path) if self.good_proposal else None, "staged": {"dest": str(dest)}}
        return [rec]

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        args = [str(a) for a in args]
        self.commands.append(args)
        if args[0] == "powershell":
            self.notes.append(args[-1])
            return 0, "", ""
        if args[0] == "git":
            if args[1] == "clone":
                subprocess.run(args, check=True, capture_output=True, timeout=60)
            return 0, "", ""
        if args[0] == "gh":
            return self.fake_gh(args)
        return self.fake_python(args[args.index("-m") + 1], args[args.index("-m") + 2:])

    def fake_gh(self, args):
        if args[2] == "list":
            head = args[args.index("--head") + 1]
            return 0, json.dumps([{"number": self.prs[head]}] if head in self.prs else []), ""
        if args[2] == "create":
            head = args[args.index("--head") + 1]
            self.prs[head] = 900 + len(self.prs)
            return 0, f"https://github.com/x/y/pull/{self.prs[head]}\n", ""
        return 0, "", ""

    def fake_python(self, module, args):
        if module == "harness.surface":
            return self.surface_code, "当たり 0 件\n", ""
        if module == "harness.fastsuite":
            return self.suite_code, "FAIL: x\n" if self.suite_code else "", ""
        if module == "harness.bench_chain" and args[0] == "stage":
            tag = args[args.index("--tag") + 1]
            d = self.local / "bench" / "chains" / f"{tag}-feedfacefeedface"
            d.mkdir(parents=True, exist_ok=True)
            return 0, f"{d}\n", ""
        if module == "harness.bench_chain" and args[0] == "finish":
            return self.finish_chain(args[args.index("--tag") + 1])
        if module == "harness.bench_pair" and args[0] == "finish":
            return self.finish_pair(args[args.index("--tag") + 1])
        if module == "harness.argon_eval":
            self.write_argon(args[1])
        return 0, "", ""

    def finish_chain(self, tag):
        if tag.endswith("-held"):
            self.write_held(tag, self.held_ok)
            d = self.run_dir / "bench" / "records" / "chains" / tag
            d.mkdir(parents=True, exist_ok=True)
            (d / "B05.json").write_text(json.dumps({"tag": tag, "totals": {"passed": self.held_ok}}), encoding="utf-8")
            return 0, "", ""
        d = self.run_dir / "bench" / "records" / "chains" / tag
        d.mkdir(parents=True, exist_ok=True)
        rows = [row(i + 1, ok=i < self.dev_ok, tokens=self.dev_tokens) for i in range(18)]
        for i, b in enumerate(DEV_BASES):
            (d / f"{b}.json").write_text(json.dumps({"tag": tag, "base": b, "stages": rows[i * 6:(i + 1) * 6]}), encoding="utf-8")
        return 0, "", ""

    def finish_pair(self, tag):
        side = tag.rsplit("-", 1)[1]
        d = self.run_dir / "bench" / "records" / "chains" / tag
        d.mkdir(parents=True, exist_ok=True)
        stages = self.state()["pair"]["stages"]
        rows = {}
        for s in stages:
            b, k = s.split(":")
            rows.setdefault(b, []).append(row(int(k), ok=self.pair[side]))
        for b, stage_rows in rows.items():
            (d / f"{b}.json").write_text(json.dumps({"tag": tag, "base": b, "stages": stage_rows}), encoding="utf-8")
        return 0, "", ""

    def write_argon(self, target):
        path = Path(base.machine.read_json(steps.hline_base.CONFIG)["argon"]) / "evals.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "post", "target": target, "decision": self.argon, "rationale": "判断点の文"}, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------- 手順

    def start(self, cid="c1", accept=True):
        with mock.patch("builtins.print"):
            return cycle.start(cid, accept)

    def tick(self):
        with mock.patch("builtins.print"):
            return cycle.tick()

    def state(self, cid="c1"):
        return base.load(cid)

    def walk(self, stop_at=None, limit=12):
        """ジョブを終わらせながら見回りを進める。stop_at の段階に来るか、周が走らなくなったら戻る。"""
        for _ in range(limit):
            self.tick()
            st = self.state()
            if st["status"] != base.RUNNING or st["phase"] == stop_at:
                return st
            self.finish_jobs()
        self.fail("見回りが終わりません")

    def ran(self, *words):
        return [c for c in self.commands if all(w in c for w in words)]


class Start(Base):
    def test_it_fixes_the_version_and_cleans_the_clone(self):
        (self.run_dir / "bench" / "records" / "leftover.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.start(), 0)
        st = self.state()
        self.assertEqual(st["rev"], git(self.run_dir, "rev-parse", "HEAD").strip())
        self.assertEqual((st["phase"], st["status"], base.active()), ("propose", base.RUNNING, "c1"))
        self.assertFalse((self.run_dir / "bench" / "records" / "leftover.json").exists())

    def test_it_refuses_bad_ids_a_running_cycle_and_a_used_id(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(self.start("C 1"), 2)
            self.assertEqual(self.start("jobs"), 2)
            self.assertEqual(self.start("c1"), 0)
            self.assertEqual(self.start("c2"), 2)
            base.set_active(None)
            self.assertEqual(self.start("c1"), 2)

    def test_a_missing_baseline_stops_the_start(self):
        shutil.rmtree(self.local / "bench" / "chains" / "l6n-dev2-0123456789abcdef")
        with mock.patch("sys.stderr") as err:
            self.assertEqual(cycle.start("c1", True), 2)
        self.assertIn("材料の置き場", "".join(str(c) for c in err.write.call_args_list))
        self.assertIsNone(base.active())

    def test_a_change_that_touches_the_implementer_needs_accepting(self):
        (self.run_dir / "harness" / "hline_prompt.py").write_bytes(b"# changed\n" + PROMPT.encode("utf-8"))
        git(self.run_dir, "add", "-A")
        git(self.run_dir, "commit", "-q", "-m", "change")
        git(self.run_dir, "push", "-q", "origin", "main")
        with mock.patch("sys.stderr") as err:
            self.assertEqual(cycle.start("c1", False), 2)
        self.assertIn("accept-diff", "".join(str(c) for c in err.write.call_args_list))
        self.assertEqual(self.start(accept=True), 0)


class Tick(Base):
    def test_nothing_runs_without_a_cycle(self):
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.commands, [])

    def test_it_runs_only_from_the_dedicated_clone(self):
        self.start()
        self.patch(cycle, "ROOT", ROOT)
        with mock.patch("sys.stderr"):
            self.assertEqual(self.tick(), 2)
        self.assertEqual(self.jobs, {})

    def test_a_stopped_cycle_stays_stopped(self):
        self.start()
        st = self.state()
        st["status"] = base.STOPPED
        base.save(st)
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.jobs, {})


class Whole(Base):
    def test_a_whole_cycle_ends_with_two_prs_and_no_merge(self):
        self.start()
        st = self.walk()
        self.assertEqual((st["status"], st["phase"], st["outcome"]), (base.DONE, "done", "取り込みの候補"))
        self.assertIsNone(base.active())
        self.assertEqual(sorted(self.prs), ["cycle/c1", "cycle/c1-records"])
        draft = self.ran("gh", "pr", "create", "--draft")
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft[0][draft[0].index("--head") + 1], "cycle/c1")
        self.assertEqual(len(self.ran("gh", "pr", "ready")), 1)
        pushed = [c[c.index("origin") + 1] for c in self.ran("git", "push")]
        self.assertEqual(set(pushed), {"cycle/c1", "cycle/c1-records"})
        self.assertEqual(self.ran("merge"), [])
        self.assertEqual(self.ran("gh", "pr", "merge"), [])

    def test_the_phases_go_in_the_planned_order_with_the_right_accounts(self):
        self.start()
        self.tick()
        self.assertEqual(self.state()["phase"], "propose")
        self.assertEqual(self.prepared[0][0], ["l6n-dev", "l6n-dev2"])   # 提案役は今の基準の 2 回を読む
        order = []
        for _ in range(8):
            self.finish_jobs()
            self.tick()
            order.append(self.state()["phase"])
        self.assertEqual(order[:3], ["pair", "dev", "held"])
        self.assertEqual(list(self.jobs), ["c1-propose1-1", "c1-pair-1", "c1-dev-1", "c1-held-1"])

    def test_the_new_clone_is_on_the_new_version(self):
        """面の設定は作業ツリーから読まれるので、新版のクローンの作業ツリーが新版でなければ、黙って旧版の値で走る。"""
        self.start()
        st = self.walk(stop_at="pair")
        self.tick()
        new = self.local / "bench" / "cycle" / "c1" / "new"
        self.assertEqual(git(new, "rev-parse", "HEAD").strip(), st["head"])
        self.assertIn('"max_attempts": 3', (new / "config" / "surface.json").read_text(encoding="utf-8"))
        old = self.local / "bench" / "cycle" / "c1" / "old"
        self.assertEqual(git(old, "rev-parse", "HEAD").strip(), st["rev"])
        self.assertIn('"max_attempts": 2', (old / "config" / "surface.json").read_text(encoding="utf-8"))

    def test_the_runner_clone_keeps_its_working_tree(self):
        self.start()
        rev = self.state()["rev"]
        self.walk()
        self.assertEqual(git(self.run_dir, "rev-parse", "HEAD").strip(), rev)

    def test_the_diff_is_only_the_surface(self):
        self.start()
        st = self.walk()
        self.assertEqual(git(self.run_dir, "diff", "--name-only", st["rev"], st["head"]).split(), ["config/surface.json"])

    def test_the_pair_job_runs_from_each_side_and_finish_reads_from_the_runner(self):
        self.start()
        st = self.walk(stop_at="pair")
        self.tick()
        cmds = self.jobs["c1-pair-1"]["cmds"]
        self.assertEqual(cmds[0], steps.PINNED)
        self.assertIn("--stages B08:4", cmds[1])   # 揺れた B09:6 は走らせない
        self.assertIn(f"--old-rev {st['rev']}", cmds[1])
        self.assertIn(f"--new-rev {st['head']}", cmds[1])
        self.assertIn("--from l6n-dev ", cmds[1])
        self.assertEqual(self.jobs["c1-pair-1"]["cwd"], str(self.local / "bench" / "cycle" / "c1" / "new"))
        self.finish_jobs()
        self.tick()
        finish = self.ran("harness.bench_pair", "finish")
        self.assertTrue(all(c[c.index("--repo") + 1] == str(self.run_dir) for c in finish))

    def test_one_target_stage_runs_twice_from_each_baseline(self):
        self.proposal_text = ONE_STAGE
        self.start()
        self.walk(stop_at="pair")
        self.tick()
        cmds = self.jobs["c1-pair-1"]["cmds"]
        self.assertEqual(len(cmds), 3)
        self.assertIn("--tag c1-p1 ", cmds[1])
        self.assertIn("--from l6n-dev ", cmds[1])
        self.assertIn("--tag c1-p2 ", cmds[2])
        self.assertIn("--from l6n-dev2 ", cmds[2])
        self.finish_jobs()
        self.tick()
        self.assertEqual(self.state()["pair"]["fixed"], 2)

    def test_a_high_verdict_stays_draft(self):
        self.argon = "HIGH"
        self.start()
        st = self.walk()
        self.assertEqual(self.ran("gh", "pr", "ready"), [])
        self.assertEqual(st["pr"]["argon"][0], "HIGH")

    def test_argon_is_called_with_tools_and_the_material_after_the_mirror_sync(self):
        self.start()
        self.walk()
        names = [c[c.index("-m") + 1] for c in self.commands if "-m" in c]
        self.assertLess(names.index("harness.main_mirror"), names.index("harness.argon_eval"))
        call = self.ran("harness.argon_eval")[0]
        self.assertIn("--tools", call)
        self.assertIn("--with", call)

    def test_the_proposal_text_never_reaches_a_public_body(self):
        self.proposal_text = PROPOSAL.replace("## 狙う失敗の型\n型", "## 狙う失敗の型\n手元だけの文")
        self.start()
        self.walk()
        bodies = [p.read_text(encoding="utf-8") for p in (self.cycle_home / "c1").glob("*.md")]
        self.assertTrue(bodies)
        self.assertFalse(any("手元だけの文" in b for b in bodies))

    def test_the_records_pr_has_only_this_cycle(self):
        self.start()
        st = self.walk()
        names = git(self.run_dir, "show", "--name-only", "--format=", st["records_head"]).split()
        self.assertIn("bench/records/cycles/c1.json", names)
        self.assertTrue(all(n.startswith(("bench/records/chains/c1-", "bench/records/pairs/c1-", "bench/records/cycles/c1")) for n in names), names)
        doc = json.loads(git(self.run_dir, "show", f"{st['records_head']}:bench/records/cycles/c1.json"))
        self.assertEqual((doc["outcome"], doc["dev"]["passed"], doc["held"]["passed"]), ("取り込みの候補", 16, 14))

    def test_held_materials_do_not_stay_after_the_run(self):
        self.start()
        st = self.walk()
        self.assertFalse(Path(st["held"]["staged"]).exists())
        self.assertTrue(Path(st["dev"]["staged"]).exists())


class Rejected(Base):
    def assert_rejected(self, st, kind, pushed=("cycle/c1-records",)):
        self.assertEqual((st["status"], st["outcome"]), (base.DONE, f"取り込まない：{kind}"))
        self.assertEqual(st["reject"]["kind"], kind)
        self.assertEqual(set(c[c.index("origin") + 1] for c in self.ran("git", "push")), set(pushed))
        self.assertEqual(self.ran("gh", "pr", "create", "--draft"), [])
        self.assertEqual(self.ran("harness.argon_eval"), [])

    def test_a_proposal_whose_diff_cannot_apply(self):
        self.proposal_text = PROPOSAL.replace('{"max_attempts": 3}', '{"max_attempts": 2}')
        self.start()
        self.assert_rejected(self.walk(), "差分")

    def test_a_surface_hit(self):
        self.surface_code = 1
        self.start()
        self.assert_rejected(self.walk(), "面の検査")

    def test_a_failing_suite(self):
        self.suite_code = 1
        self.start()
        self.assert_rejected(self.walk(), "試験")
        self.assertFalse((self.cycle_home / "wt").exists())

    def test_no_fix_in_the_pair(self):
        self.pair = {"old": False, "new": False}
        self.start()
        st = self.walk()
        self.assert_rejected(st, "対の実行")
        self.assertEqual(self.ran("harness.bench_chain", "stage"), [])   # 回帰の守りを走らせない

    def test_dev_below_the_line(self):
        self.dev_ok = 10
        self.start()
        st = self.walk()
        self.assert_rejected(st, "回帰の守り：開発用")
        self.assertEqual(self.ran("--set", "held"), [])

    def test_held_below_the_line(self):
        self.held_ok = 9
        self.start()
        st = self.walk()
        self.assert_rejected(st, "回帰の守り：held-out")
        self.assertFalse(Path(st["held"]["staged"]).exists())

    def test_the_public_record_has_the_fixed_word_not_the_detail(self):
        self.surface_code = 1
        self.start()
        st = self.walk()
        doc = git(self.run_dir, "show", f"{st['records_head']}:bench/records/cycles/c1.json")
        self.assertIn("面の検査", doc)
        self.assertNotIn("当たり 0 件", doc)
        self.assertIn("当たり 0 件", st["reject"]["detail"])


class CostLine(Base):
    def write_run(self, out, tag, k, tokens):
        d = Path(out) / "runs" / "B07" / f"factory-{tag}-s{k}" / "factory"
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps({"tokens": {"total": tokens}}), encoding="utf-8")

    def test_the_run_is_stopped_when_it_crosses_the_line(self):
        self.start()
        self.walk(stop_at="dev")
        self.tick()
        new_out = self.local / "bench" / "cycle" / "c1" / "new" / ".bench"
        self.write_run(new_out, "c1-dev", 1, 10_000_000)
        self.tick()
        self.assertEqual((self.stopped, self.state()["phase"]), (0, "dev"))   # 線の下なら止めない
        self.write_run(new_out, "c1-dev", 2, 20_000_000)   # 基準 1,800 万の 1.5 倍は 2,700 万
        self.tick()
        st = self.state()
        self.assertEqual(self.stopped, 1)
        self.assertEqual((st["status"], st["outcome"]), (base.DONE, "取り込まない：費用の線"))

    def test_spent_ignores_broken_records(self):
        out = self.tmp / "o"
        self.write_run(out, "t", 1, 5)
        self.write_run(out, "t", 2, "many")
        broken = out / "runs" / "B07" / "factory-t-s3" / "factory"
        broken.mkdir(parents=True)
        (broken / "run.json").write_text("not json", encoding="utf-8")
        self.write_run(out, "other", 1, 100)
        self.assertEqual(base.spent(out, ["t"]), 5)


class Stops(Base):
    def test_no_agreed_stage_asks_the_pilot(self):
        self.proposal_text = PROPOSAL.replace("- B08 s4\n- B09 s6\n", "- B09 s6\n")   # 揺れたステージだけ
        self.start()
        st = self.walk()
        self.assertEqual((st["status"], st["phase"]), (base.STOPPED, "diff"))
        self.assertIn("そろったステージ", st["reason"])
        self.assertEqual(len(self.notes), 1)

    def test_a_lost_job_is_started_again_with_a_new_id_then_stops(self):
        self.start()
        self.walk(stop_at="pair")
        self.tick()
        for expected in ("c1-pair-1", "c1-pair-2", "c1-pair-3"):
            self.assertIn(expected, self.jobs)
            self.jobs[expected]["state"] = "lost"
            self.tick()
            if expected != "c1-pair-3":
                self.tick()
        st = self.state()
        self.assertEqual((st["status"], st["phase"]), (base.STOPPED, "pair"))
        self.assertIn("3 回とも", st["reason"])

    def test_a_failed_job_exit_is_retried_like_a_lost_one(self):
        self.start()
        self.walk(stop_at="pair")
        self.tick()
        self.finish_jobs(exit_code=2)
        self.tick()
        self.tick()
        self.assertIn("c1-pair-2", self.jobs)

    def test_resume_continues_from_the_same_phase_with_a_new_job_id(self):
        self.start()
        self.walk(stop_at="pair")
        self.tick()
        for i in (1, 2, 3):
            self.jobs[f"c1-pair-{i}"]["state"] = "lost"
            self.tick()
            self.tick()
        self.assertEqual(self.state()["status"], base.STOPPED)
        with mock.patch("builtins.print"):
            self.assertEqual(cycle.resume(), 0)
        self.tick()
        self.assertIn("c1-pair-4", self.jobs)
        self.finish_jobs()
        st = self.walk()
        self.assertEqual(st["status"], base.DONE)

    def test_resume_refuses_while_the_task_runs(self):
        self.start()
        st = self.state()
        st["status"] = base.STOPPED
        base.save(st)
        base.bench_job.task_state.return_value = ("Running", "0")
        with mock.patch("sys.stderr"):
            self.assertEqual(cycle.resume(), 2)

    def test_a_public_text_hit_stops_before_the_push(self):
        self.start()
        real = steps.cp.public_problems
        self.patch(steps.cp, "public_problems", side_effect=lambda name, text, w, l: ["漏れ: x:1"] if name == "PR の本文" else real(name, text, w, l))
        st = self.walk()
        self.assertEqual((st["status"], st["phase"]), (base.STOPPED, "pr"))
        self.assertEqual(self.ran("git", "push"), [])
        self.assertEqual(self.ran("gh", "pr", "create"), [])


class Propose(Base):
    def test_a_discarded_proposal_is_tried_once_more_later_then_stops(self):
        self.good_proposal = False
        self.start()
        self.tick()
        self.finish_jobs()
        self.tick()
        st = self.state()
        self.assertEqual((st["status"], st["propose"]["attempt"], st["propose"]["dest"]), (base.RUNNING, 2, None))
        self.assertGreater(st["propose"]["not_before"], 0)
        self.tick()
        self.assertEqual(len(self.prepared), 1)   # 時間を置く
        st["propose"]["not_before"] = 0
        base.save(st)
        self.tick()
        self.assertEqual(len(self.prepared), 2)
        self.finish_jobs()
        self.tick()
        st = self.state()
        self.assertEqual(st["status"], base.STOPPED)
        self.assertIn("2 回とも", st["reason"])

    def test_a_lost_job_discards_the_copy_and_prepares_a_new_one(self):
        self.start()
        self.tick()
        dest = self.prepared[0][1]
        self.jobs["c1-propose1-1"]["state"] = "lost"
        self.tick()
        self.assertFalse(dest.exists())
        st = self.state()
        self.assertEqual((st["propose"]["attempt"], st["propose"]["dest"]), (2, None))

    def test_a_crash_after_collecting_does_not_lose_the_proposal(self):
        self.start()
        self.tick()
        self.finish_jobs()
        dest = self.prepared[0][1]
        recs = self.collect(None, dest)   # 取り込んで写しを消した後に落ちた
        store = steps.pl.store_dir(base.machine.read_json(steps.hline_base.CONFIG))
        store.mkdir(parents=True, exist_ok=True)
        (store / "r1.json").write_text(json.dumps(recs[0], ensure_ascii=False), encoding="utf-8")
        self.patch(steps.hline_base, "load_config", side_effect=base.machine.read_json)
        self.tick()
        self.assertEqual(self.state()["phase"], "pair")


class Abort(Base):
    def test_abort_stops_the_job_discards_the_copy_and_clears_active(self):
        self.start()
        self.tick()
        dest = self.prepared[0][1]
        base.bench_job.task_state.return_value = ("Running", "0")
        with mock.patch("builtins.print"):
            self.assertEqual(cycle.abort(), 0)
        self.assertEqual(self.stopped, 1)
        self.assertFalse(dest.exists())
        self.assertIsNone(base.active())
        self.assertEqual(self.state()["status"], base.ABORTED)

    def test_status_prints_the_phase_and_the_reason(self):
        with mock.patch("builtins.print") as p:
            self.assertEqual(cycle.status(), 0)
        self.assertIn("周はまだありません", str(p.call_args))
        self.start()
        st = self.state()
        st.update(status=base.STOPPED, reason="理由の文")
        base.save(st)
        with mock.patch("builtins.print") as p:
            cycle.status()
        text = str(p.call_args_list)
        self.assertIn("段階 propose", text)
        self.assertIn("理由の文", text)


class JobIds(unittest.TestCase):
    def test_bench_job_refuses_a_used_id_so_retries_must_change_it(self):
        self.assertIn("同じ id", Path(bench_job.__file__).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
