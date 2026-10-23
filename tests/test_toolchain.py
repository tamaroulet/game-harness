"""環境の唯一の台帳（W-126、ADR-004 の束 F）：版は config/toolchain.json にだけ書き、ほかのものはそこから作るか読む。
この PC に固有の絶対パスは Git の外の machine.json にだけ置き、設定は ${名前} で書く。
実物の版が台帳と違えば envcheck が止まり、理由を 1 行で出す。envcheck --github は main の規則を確かめる。

    python -m unittest tests.test_toolchain -v
"""
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import envcheck  # noqa: E402
import envscan  # noqa: E402
import machine  # noqa: E402
import quiet  # noqa: E402
import toolchain  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "harness-tests.yml"
RULES_OK = [{"type": "deletion"}, {"type": "pull_request", "parameters": {}},
            {"type": "required_status_checks",
             "parameters": {"required_status_checks": [{"context": "test"}, {"context": "approval"}]}}]


class Ledger(unittest.TestCase):
    def test_the_lock_is_generated_from_the_ledger(self):
        self.assertEqual(toolchain.LOCK.read_text(encoding="utf-8"), toolchain.lock_text(),
                         "requirements.lock を python -m harness.toolchain lock で書き直す")
        self.assertTrue(toolchain.lock_text().isascii(), "pip がコードページ 932 でも読めるように ASCII だけで書く")

    def test_envcheck_pins_come_from_the_ledger(self):
        self.assertEqual(envcheck.PINNED, toolchain.TOOLCHAIN)
        self.assertFalse((ROOT / "config" / "environment.json").exists(), "旧の固定値は台帳に統合して消した")
        pinned = envcheck.load_pinned()
        self.assertEqual(envcheck.lock_packages(), {k.lower(): v for k, v in pinned["python_packages"].items()})
        self.assertIn("mujoco", pinned["python_packages"], "CI が直に入れていたものも台帳に入れる")

    def test_the_lock_must_match_or_envcheck_refuses(self):
        with mock.patch.object(envcheck, "lock_packages", return_value={"pyyaml": "6.0.3"}):
            with self.assertRaises(envcheck.EnvError):
                envcheck.load_pinned()

    def test_version_literals_are_only_in_the_ledger_and_the_boundary(self):
        base = envscan.load_baseline()
        found = {f: k["version_literal"] for f, k in envscan.scan(envscan.ROOT, base["boundary"]).items()
                 if k.get("version_literal")}
        self.assertEqual(found, {}, "版の文字列は config/toolchain.json に書き、そこから読む")

    def test_ci_installs_and_warms_up_from_the_ledger(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.lock", text)
        self.assertIn("python -m harness.toolchain warmup", text)
        self.assertNotRegex(text, r"==\d", "CI に pip の版を直に書かない")
        self.assertNotIn("Version=", text, "CI に NuGet の版を直に書かない")
        major_minor = ".".join(toolchain.load()["python"].split(".")[:2])
        self.assertEqual(re.findall(r"python-version:\s*'([^']+)'", text), [major_minor])


class Machine(unittest.TestCase):
    def test_absolute_paths_are_only_in_the_boundary(self):
        base = envscan.load_baseline()
        found = {f: k["abs_path"] for f, k in envscan.scan(envscan.ROOT, base["boundary"]).items() if k.get("abs_path")}
        self.assertEqual(found, {}, "絶対パスは machine.json に置き、設定は ${名前} で書く")

    def test_every_name_in_the_configs_is_defined(self):
        known = set(machine.names(machine.EXAMPLE))
        for p in envscan.files():
            for name in machine.VAR_RE.findall(p.read_text(encoding="utf-8")):
                self.assertIn(name, known, f"{envscan.rel(p)} の ${{{name}}} が config/machine.example.json に無い")

    def test_expand(self):
        table = {"local": str(Path("/m/.local"))}
        self.assertEqual(machine.expand("${local}/out/{project}/x", table),
                         str(Path("/m/.local/out/{project}/x")), "替えた後も {project} は残す")
        self.assertEqual(machine.expand({"a": ["${local}", 3, "plain/path"]}, table),
                         {"a": [str(Path("/m/.local")), 3, "plain/path"]}, "${ の無い文字列はそのまま")
        with self.assertRaises(machine.MachineError):
            machine.expand("${nowhere}/x", table)

    def test_machine_json_is_found_by_env_and_expands_variables(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.json"
            f.write_text(json.dumps({"paths": {"local": "%HARNESS_TEST_HOME%/lcl"}}), encoding="utf-8")
            with mock.patch.dict("os.environ", {machine.ENV: str(f), "HARNESS_TEST_HOME": d}):
                self.assertEqual(machine.source(), f)
                self.assertEqual(machine.path("local"), Path(d) / "lcl")
                self.assertEqual(machine.names()["unity_editor"], toolchain.load()["unity_editor"])
            # %VAR% は OS に依らず替える（os.path.expandvars は Windows の外で %VAR% を替えない）
            with mock.patch.object(machine.os.path, "expandvars", side_effect=AssertionError), \
                    mock.patch.dict("os.environ", {"HARNESS_TEST_HOME": d}):
                self.assertEqual(machine.env_vars("%HARNESS_TEST_HOME%/a;%HARNESS_NO_SUCH_VAR%"),
                                 f"{d}/a;%HARNESS_NO_SUCH_VAR%")
            with mock.patch.dict("os.environ", {machine.ENV: str(Path(d) / "absent.json")}):
                self.assertEqual(machine.source(), machine.EXAMPLE, "無ければ例で代用する")

    def test_a_missing_machine_json_fails_envcheck(self):
        pinned = {"cli": {}}
        self.assertTrue(any("machine.json がありません" in p for p in envcheck.problems({"machine_json": False}, pinned)))
        self.assertEqual(envcheck.problems({"machine_json": True}, pinned), [])

    def test_the_loaders_return_no_logical_names(self):
        import agy_pinned
        import bench_score
        import hline_base
        import project
        import research
        loaded = [hline_base.load_config(), research.load_config(), agy_pinned.load_record(),
                  project.config("scheduler"), project.config("decompose"), str(bench_score.oracles_root())]
        for pid in ("falling-blocks", "unity-2d"):
            proj = project.load(pid)
            loaded += [proj, project.pipeline_config(proj)]
        for doc in loaded:
            self.assertNotIn("${", json.dumps(doc, ensure_ascii=False))


class Csproj(quiet.Quiet, unittest.TestCase):
    def test_warmup_is_written_from_the_ledger(self):
        tc = toolchain.load()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(toolchain.main(["warmup", d]), 0)
            text = (Path(d) / "Warmup.csproj").read_text(encoding="utf-8")
        self.assertIn(f"<TargetFramework>{tc['csproj']['target_framework']}</TargetFramework>", text)
        for name, ver in tc["csproj"]["packages"].items():
            self.assertIn(f'<PackageReference Include="{name}" Version="{ver}" />', text)

    def test_lock_check_fails_when_the_lock_drifts(self):
        with tempfile.TemporaryDirectory() as d:
            lock = Path(d) / "requirements.lock"
            lock.write_text("pyyaml==0.0.1\n", encoding="utf-8")
            with mock.patch.object(toolchain, "LOCK", lock):
                self.assertEqual(toolchain.main(["lock", "--check"]), 1)
                self.assertEqual(toolchain.main(["lock"]), 0)
                self.assertEqual(toolchain.main(["lock", "--check"]), 0)
            self.assertEqual(lock.read_bytes(), toolchain.lock_text().encode("utf-8"))

    def test_harness_built_csprojs_use_the_ledger(self):
        import invrun
        import witness_selftest
        refs = toolchain.package_references()
        self.assertIn(refs, witness_selftest.files()["W.csproj"])
        with tempfile.TemporaryDirectory() as d:
            sandbox, work = Path(d) / "sandbox", Path(d) / "work"
            sandbox.mkdir()
            csproj = invrun.materialize(work, sandbox, "Core", {}, cfg={"lang_version": "9.0"})
            text = csproj.read_text(encoding="utf-8")
        self.assertIn(refs, text)
        self.assertIn(f"<TargetFramework>{toolchain.target_framework()}</TargetFramework>", text)


class Mismatch(quiet.Quiet, unittest.TestCase):
    def test_a_cli_that_differs_from_the_ledger_stops_with_one_line(self):
        pinned = envcheck.load_pinned()
        measured = {k: json.loads(json.dumps(pinned.get(k))) for k in
                    ("os", "python", "python_packages", "cli", "tools", "test_packages")}
        measured["cli"]["claude"] = "0.0.1"
        with mock.patch.object(envcheck, "measure", return_value=measured), \
                mock.patch.object(envcheck, "game_csproj", return_value=Path("x.csproj")):
            rc = envcheck.main([])
        self.assertNotEqual(rc, 0)
        lines = self.written().strip().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"^NG: .*cli\.claude：実測 '0\.0\.1'")

    def test_every_cli_in_the_ledger_is_measured(self):
        seen = []
        fake = lambda args, **kw: seen.append(args[0]) or SimpleNamespace(returncode=0, stdout="1.2.3\n", stderr="")
        envcheck.measure(clis=tuple(toolchain.load()["cli"]), run=fake)
        self.assertEqual(seen, list(toolchain.load()["cli"]))


class GitHub(quiet.Quiet, unittest.TestCase):
    def fake(self, rules=None, rc=0, err=""):
        self.calls = []

        def run(args, **kw):
            self.calls.append(args)
            return SimpleNamespace(returncode=rc, stdout=json.dumps(rules or []), stderr=err)
        return run

    def test_protected_main_passes(self):
        self.assertEqual(envcheck.github(self.fake(RULES_OK)), [])
        self.assertEqual(self.calls[0][:2], [envcheck.FORGE_CLI, "api"])
        with mock.patch.object(envcheck, "github", return_value=[]):
            self.assertEqual(envcheck.main(["--github"]), 0)

    def test_missing_rules_are_named(self):
        rules = [r for r in RULES_OK if r["type"] != "pull_request"]
        rules[-1] = {"type": "required_status_checks",
                     "parameters": {"required_status_checks": [{"context": "test"}]}}
        self.assertEqual(envcheck.github(self.fake(rules)), ["PR の必須が掛かっていない", "必須チェックに approval が無い"])
        with mock.patch.object(envcheck, "github", return_value=["PR の必須が掛かっていない"]):
            self.assertEqual(envcheck.main(["--github"]), 1)
        self.assertEqual(len(self.written().strip().splitlines()), 1)

    def test_an_unanswered_query_is_not_a_pass(self):
        with self.assertRaises(envcheck.EnvError):
            envcheck.github(self.fake(rc=1, err="gh: Not Found (HTTP 404)"))
        with mock.patch.object(envcheck, "github", side_effect=envcheck.EnvError("問えません")):
            self.assertEqual(envcheck.main(["--github"]), 1)


if __name__ == "__main__":
    unittest.main()
