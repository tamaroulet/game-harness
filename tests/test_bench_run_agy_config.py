"""工廠の走行の実装役を設定のファイルで agy に替える入口（W-101）の試験。
本物の claude・agy の CLI は使わず、子プロセス（proc.run）を偽物に差し替える。git は一時のディレクトリのリポジトリだけ。
工廠の偽物の組み立ては tests/test_bench_run_factory.py のものを借りる（継いだ試験そのものは走らせない）。"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "harness"))
sys.path.insert(0, str(HERE))
import bench_run  # noqa: E402
import test_bench_run_factory as tf  # noqa: E402

ROOT = HERE.parent
AGY = "gemini-3.8-flash-medium"
CLAUDE_AUX = ["claude-haiku-"]
AGY_EXPECTED = {
    "cli": "agy", "headless_flag": "-p", "auto_approve_flag": "--dangerously-skip-permissions", "model_flag": "--model",
    "model_name": AGY, "output_format_args": ["--output-format", "stream-json"], "extra_flags": ["--sandbox"],
    "usage_format": "agy", "version": "1.2.14", "budget": {"max_turns": 15, "resume_max_turns": 8},
}


def claude_role():
    return {"cli": "claude", "model": tf.MODEL, "auxiliary_models": list(CLAUDE_AUX)}


class ConfigFileTests(unittest.TestCase):
    def test_agy_config_values(self):
        doc = json.loads((ROOT / "config" / "bench_implementer_agy.json").read_text(encoding="utf-8"))
        self.assertEqual(doc, AGY_EXPECTED)
        line = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["budget"], line["implementer"]["budget"])

    def test_line_config_replaces_only_implementer(self):
        base = {"implementer": {"cli": "claude"}, "oracles": "O"}
        work = Path("w")
        plain = bench_run.line_config(base, work, work / "inbox", "abc", "br")
        swapped = bench_run.line_config(base, work, work / "inbox", "abc", "br", AGY_EXPECTED)
        self.assertEqual(plain["implementer"], {"cli": "claude"})
        self.assertEqual(swapped["implementer"], AGY_EXPECTED)
        self.assertEqual({k: v for k, v in swapped.items() if k != "implementer"},
                         {k: v for k, v in plain.items() if k != "implementer"})


class AgyFactoryTests(tf.FactoryTests):
    agy_version = "1.2.14"
    implementer = None

    def build_repo(self, freeze=None):
        cfg = {"implementer": claude_role(), "planner": claude_role(), "reviewer": claude_role(), "oracles": "O"}
        self.repo.mkdir()
        tf.sh(self.repo, "init", "-q")
        files = {"docs/bench/B1/spec.md": tf.SPEC, "calc/__init__.py": "x = 1\n", "harness/tool.py": "t = 1\n",
                 "config/hline.json": json.dumps(cfg)}
        if freeze is not None:
            files["bench/records/freeze.json"] = json.dumps({"cli_version": freeze})
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        tf.sh(self.repo, "add", "-A")
        tf.sh(self.repo, "commit", "-q", "-m", "base")
        return tf.sh(self.repo, "rev-parse", "HEAD")

    def fake_run(self, args, cwd, ttl, label, env=None, input=None):
        if args[-1] == "--version":
            return 0, (self.agy_version if args[0] == "agy" else self.cli_version) + "\n", ""
        return super().fake_run(args, cwd, ttl, label, env=env, input=input)

    def invoke(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                mock.patch.object(bench_run.proc, "run", self.fake_run), \
                mock.patch.object(bench_run.proc, "resolve_cli", lambda name: [name]), \
                mock.patch.object(bench_run, "clock", self.clock), mock.patch.object(bench_run, "sleep", self.clock.sleep):
            return bench_run.main(argv)

    def factory_rc(self):
        extra = ["--implementer", str(self.implementer)] if self.implementer else []
        return self.invoke(self.argv("factory", ["--base", self.base, "--sessions", str(self.sessions), *extra]))

    def run_with(self, model, implementer=ROOT / "config" / "bench_implementer_agy.json", **kw):
        self.implementer = implementer
        polls = [{**p, "impl_model": model} for p in tf.HAPPY]
        self.assertEqual(self.go(polls=polls, freeze="2.0.0", **kw), 0)

    def env(self):
        return json.loads((self.rundir / "env.json").read_text(encoding="utf-8"))

    # ---- 合格条件 2
    def test_implementer_replaced_and_recorded(self):
        self.run_with(AGY)
        want = json.loads((ROOT / "config" / "bench_implementer_agy.json").read_text(encoding="utf-8"))
        self.assertEqual(self.cfgs[-1]["implementer"], want)
        self.assertEqual(self.cfgs[-1]["planner"], claude_role())
        saved = json.loads((self.rundir / "factory" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, self.cfgs[-1])

    def test_without_implementer_nothing_changes(self):
        self.implementer = None
        self.assertEqual(self.go(freeze="2.0.0"), 0)
        self.assertEqual(self.cfgs[-1]["implementer"], claude_role())
        self.assertFalse((self.rundir / "factory" / "config.json").exists())

    # ---- 合格条件 3
    def test_versions_match_and_both_recorded(self):
        self.run_with(AGY)
        self.assertFalse(self.run_json()["invalid"])
        env = self.env()
        self.assertEqual(env["cli_version"], "2.0.0 (Claude Code)")
        self.assertEqual(env["implementer_cli_version"], "1.2.14")

    def test_agy_version_mismatch_is_req03(self):
        self.agy_version = "1.2.15"
        self.run_with(AGY)
        run = self.run_json()
        self.assertEqual(run["invalid"], "REQ-03")
        self.assertIn("実装役の CLI の版が設定と違います", run["reason"])

    def test_claude_version_mismatch_is_still_req03(self):
        self.run_with(AGY, cli_version="2.9.9 (Claude Code)")
        self.assertEqual(self.run_json()["invalid"], "REQ-03")

    # ---- 合格条件 4
    def test_mixed_models_are_valid(self):
        self.run_with(AGY)
        roles = sorted(p.name.split("-", 1)[1] for p in (self.rundir / "calls").iterdir())
        self.assertEqual(roles, ["implementer"] * 3 + ["planner"] * 2 + ["reviewer"] * 2)
        self.assertEqual(self.finish()["invalid"], False)

    def test_claude_model_in_agy_implementer_is_req02(self):
        self.run_with(tf.MODEL)
        record = self.finish()
        self.assertEqual(record["invalid"], "REQ-02")
        self.assertIn(tf.MODEL, record["reason"])

    def test_no_config_record_keeps_old_judgement(self):
        self.implementer = None
        self.assertEqual(self.go(freeze="2.0.0"), 0)
        self.assertEqual(self.finish()["invalid"], False)
        self.run_with_old_agy_model()

    def run_with_old_agy_model(self):
        calls = sorted((self.rundir / "calls").glob("*-implementer"))[0]
        (calls / "result.json").write_text(json.dumps({"modelUsage": {AGY: {}}}), encoding="utf-8")
        self.assertEqual(self.finish()["invalid"], "REQ-02")


# 継いだ試験は走らせない（合格条件の試験だけを走らせる）
for _name in [n for n in dir(tf.FactoryTests) if n.startswith("test_") and n not in vars(AgyFactoryTests)]:
    setattr(AgyFactoryTests, _name, None)


if __name__ == "__main__":
    unittest.main()
