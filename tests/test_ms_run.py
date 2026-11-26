"""Multi-step SWE の実行口（harness/ms_run.py）とアダプタ（harness/ms_adapter.py）の部品の試験（docker に依らない）。

    python -m unittest tests.test_ms_run -v
"""
import collections
import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import core  # noqa: E402
import ms_adapter  # noqa: E402
import ms_run  # noqa: E402
import phase1_dev  # noqa: E402

REGISTRIES = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org", "crates.io", "proxy.golang.org", "github.com")
DEV = ("pandas-numpy-modernization", "ooxml-spreadsheet-engine-multiversion")


def tar_of(entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data, kind in entries:
            ti = tarfile.TarInfo(name)
            if kind == "sym":
                ti.type, ti.linkname = tarfile.SYMTYPE, data
                tf.addfile(ti)
            else:
                ti.size = len(data)
                tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


class ConfigTests(unittest.TestCase):
    def test_config_pins_model_registry_block_non_root_and_limits(self):
        cfg = ms_run.load_config()
        self.assertEqual(cfg["agent"]["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual(cfg["agent"]["install"]["version"], "1.5.3")
        self.assertFalse(set(REGISTRIES) & set(cfg["network"]["allow"]))
        self.assertNotIn("quota_floor", cfg, "週の残りの床（35%/45%）での予算の管理はやめた")
        self.assertNotIn("quota_floor", cfg["ms"])
        self.assertEqual(cfg["quota_guard"], {"gemini-weekly": 0.05, "gemini-5h": 0.10})
        self.assertEqual(set(k for k in cfg["ms"]["limits"] if not k.startswith("_")), {"run_tokens", "run_seconds"})
        self.assertEqual(cfg["ms"]["mem_cap_mb"], 12288)
        self.assertEqual(cfg["sandbox"]["uid"], 1000, "実装役は非 root")
        self.assertEqual(cfg["sandbox"]["workdir"], "/app")
        self.assertEqual(set(cfg["ms"]["dev_tasks"]), set(DEV))
        for task, image_id in cfg["ms"]["dev_tasks"].items():
            self.assertRegex(task, ms_run.TASK_RE)
            self.assertRegex(image_id, r"^sha256:[0-9a-f]{64}$")
            self.assertTrue(ms_run.gate_checks(cfg, task), "dev の課題ごとに第 1 関門の検査がある")
        self.assertTrue(set(cfg["ms"]["setup_net"]) <= set(cfg["ms"]["dev_tasks"]))
        self.assertFalse(hasattr(ms_run, "MEM_CAP_MB"), "メモリの上限は設定から読む")

    def test_only_dev_tasks_run(self):
        cfg = ms_run.load_config()
        self.assertEqual(ms_run.require_dev(cfg, "pandas-numpy-modernization"), "pandas-numpy-modernization")
        for bad in ("demo-vm-version-chain", "", "pandas-numpy-modernization; rm -rf /"):
            with self.assertRaises(phase1_dev.NotDevError):
                ms_run.require_dev(cfg, bad)

    def test_conditions(self):
        cfg = ms_run.load_config()
        h0 = ms_run.condition(cfg, "H0_fresh")
        self.assertEqual((h0["gate"], h0["context"]), (False, "fresh"))
        self.assertEqual(ms_run.condition(cfg, "H0_continue")["context"], "continue")
        self.assertTrue(h0["protect"], "既定は設計どおり守る")
        for name in ("H_continue", "H_handoff"):
            c = ms_run.condition(cfg, name)
            self.assertTrue(c["gate"])
            self.assertEqual(set(c["stop_rules"]), {"same_diff", "no_progress", "protect", "second"})
            self.assertFalse(c["halt_on_stop"], "ベンチの走りでは止まった段の要約を残して次へ進む")
            self.assertFalse(c["protect"], "Multi-step SWE の裁定（2026-11-26）：前の段のテストの書き換えを押し返さない")
        self.assertEqual(ms_run.condition(cfg, "H_handoff")["context"], "handoff")
        for bad in ("_comment", "H9"):
            with self.assertRaises(ValueError):
                ms_run.condition(cfg, bad)
        cfg["ms"]["conditions"]["X"] = {"gate": True, "temperature": 0}
        with self.assertRaises(ValueError):
            ms_run.condition(cfg, "X")
        cfg["ms"]["conditions"]["Y"] = {"context": "summary"}
        with self.assertRaises(ValueError):
            ms_run.condition(cfg, "Y")

    def test_gate_checks_refuse_unknown_kinds(self):
        cfg = ms_run.load_config()
        checks = ms_run.gate_checks(cfg, "pandas-numpy-modernization")
        self.assertEqual([(c.id, c.kind) for c in checks], [("pyflakes", "pyflakes_diff"), ("tests", "pytest")])
        cfg["ms"]["gate"]["pandas-numpy-modernization"] = [{"id": "j", "kind": "llm_judge"}]
        with self.assertRaises(ValueError):
            ms_run.gate_checks(cfg, "pandas-numpy-modernization")

    def test_step_unit_makes_one_requirement_over_the_required_checks(self):
        checks = [core.Check("a", "cmd", ["go", "build", "./..."]), core.Check("b", "cmd", ["true"], required=False)]
        u = ms_run.step_unit({"name": "01-x", "instruction": "do x"}, checks)
        self.assertEqual((u.ledger.unit, u.prompt), ("01-x", "do x"))
        self.assertEqual(u.ledger.requirements[0].checks, ["a"])
        self.assertIsNot(u.ledger.checks[0], checks[0], "段ごとに検査の写しを持つ")


class PartsTests(unittest.TestCase):
    def test_memory_is_capped_by_config(self):
        self.assertEqual(ms_run.limits(7, 8192, 12288), ["--cpus", "7", "--memory", "8192m", "--memory-swap", "8192m"])
        self.assertEqual(ms_run.limits(4, 16384, 12288)[3], "12288m")
        self.assertEqual(ms_run.limits(4, 16384, 20480)[3], "16384m")

    def test_image_tag_matches_the_scan_tags(self):
        self.assertEqual(ms_run.image_tag("pandas-numpy-modernization", "env"), "msscan-pandas-numpy-modernization:env")
        self.assertEqual(ms_run.image_tag("demo-gopath-to-go1.25", "step2"), "msscan-demo-gopath-to-go1.25:step2")

    def test_read_task_takes_steps_limits_and_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            t = Path(d) / "demo-task"
            for s in ("01-a", "02-b"):
                (t / "steps" / s / "tests").mkdir(parents=True)
                (t / "steps" / s / "workdir").mkdir()
                (t / "steps" / s / "instruction.md").write_text(f"# {s}\n", encoding="utf-8")
                (t / "steps" / s / "tests" / "test.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (t / "steps" / "02-b" / "workdir" / "setup.sh").write_text("true\n", encoding="utf-8")
            (t / "task.toml").write_text(
                'multi_step_reward_strategy = "mean"\n'
                'artifacts = [{ source = "/app", destination = "workspace", exclude = [".git", ".git/**"] }]\n'
                '[environment]\ncpus = 4\nmemory_mb = 8192\n[verifier]\nenvironment_mode = "separate"\n'
                '[verifier.environment]\ncpus = 2\nmemory_mb = 16384\n'
                '[[steps]]\nname = "01-a"\n[steps.agent]\ntimeout_sec = 7200.0\n[steps.verifier]\ntimeout_sec = 900.0\n'
                '[[steps]]\nname = "02-b"\n', encoding="utf-8")
            cfg = {"ms": {"data": d}}
            got = ms_run.read_task(cfg, "demo-task")
        self.assertEqual(got["exclude"], [".git", ".git/**"])
        self.assertEqual([s["name"] for s in got["steps"]], ["01-a", "02-b"])
        self.assertEqual((got["steps"][0]["agent_timeout_s"], got["steps"][0]["verifier_timeout_s"]), (7200, 900))
        self.assertEqual([s["setup"] for s in got["steps"]], [False, True])
        self.assertEqual((got["v_cpus"], got["v_memory_mb"]), (2, 16384))
        self.assertIn("tests/test.sh", got["steps"][0]["sha256"])

    def test_artifact_excludes_the_harness_files(self):
        self.assertEqual(ms_run.tar_excludes([".git"]),
                         ["--exclude=.git", "--exclude=WORKSPACE_HANDOFF.md", "--exclude=harness_progress.json"])

    def test_safe_extract_rejects_escapes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(ms_adapter.safe_extract(tar_of([("./a/b.txt", b"x", "file")]), d), 1)
            self.assertEqual((Path(d) / "a" / "b.txt").read_bytes(), b"x")
        with tempfile.TemporaryDirectory() as d:   # data の filter は頭の / を外して展開先の中に置く
            ms_adapter.safe_extract(tar_of([("/abs.txt", b"x", "file")]), d)
            self.assertTrue((Path(d) / "abs.txt").is_file())
        for bad in ([("../evil.txt", b"x", "file")], [("./ln", "/etc/passwd", "sym")], [("./ln", "../../x", "sym")]):
            with tempfile.TemporaryDirectory() as d, self.assertRaises(tarfile.FilterError):
                ms_adapter.safe_extract(tar_of(bad), d)

    def test_used_points_tokens_and_run_limits(self):
        q = lambda w: {"buckets": {"gemini-weekly": {"remaining_fraction": w}}}  # noqa: E731
        self.assertEqual(ms_run.used_pt(q(0.80), q(0.77)), 3.0)
        self.assertIsNone(ms_run.used_pt(None, q(0.7)))
        recs = [{"usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 9999}}, {"usage": None},
                {"usage": {"input_tokens": 5, "output_tokens": None}}]
        self.assertEqual(ms_run.run_tokens(recs), 125, "キャッシュの読みは数えない")
        lim = {"run_tokens": 1000, "run_seconds": 60}
        self.assertEqual(ms_run.run_limit(lim, 999, 59), (False, None))
        self.assertTrue(ms_run.run_limit(lim, 1000, 0)[0])
        self.assertTrue(ms_run.run_limit(lim, 0, 60)[0])
        self.assertEqual(ms_run.run_limit({}, 10 ** 9, 10 ** 9), (False, None))

    def test_groups_read_the_step_report(self):
        rep = {"combination": "A * B", "reward": 1.0, "families": {
            "behavior": {"corpus": {"detail": {"passed": 1236, "total": 1236}}},
            "gates": {"lint": {"score": 0.5}}}}
        self.assertEqual(ms_run.groups(rep), {"behavior": {"corpus": {"passed": 1236, "total": 1236}},
                                              "gates": {"lint": {"score": 0.5}}})
        self.assertIsNone(ms_run.groups({"reward": 1.0}))


class AdapterPartsTests(unittest.TestCase):
    A, B = "a" * 64, "b" * 64

    def test_manifest_parse_digest_and_diff(self):
        man = ms_adapter.parse_manifest(f"{self.A}  ./x.py\n{self.B}  ./d/y.py\nnoise\n")
        self.assertEqual(man, {"x.py": self.A, "d/y.py": self.B})
        self.assertEqual(ms_adapter.manifest_digest(man), ms_adapter.manifest_digest(dict(reversed(list(man.items())))))
        self.assertNotEqual(ms_adapter.manifest_digest(man), ms_adapter.manifest_digest({"x.py": self.A}))
        after = {"x.py": self.B, "z.py": self.A}
        self.assertEqual(ms_adapter.diff_manifest(man, after), (["z.py"], ["x.py"], ["d/y.py"]))

    def test_parse_pytest_summary(self):
        out = ("..F\nPASSED tests/test_x.py::test_a\nFAILED tests/test_x.py::test_b - AssertionError: 1 != 2\n"
               "ERROR tests/test_y.py - ImportError: no module\nSKIPPED [1] tests/test_z.py:3: skip\n"
               "XPASS tests/test_x.py::test_c\n")
        passed, failed, errors = ms_adapter.parse_pytest(out)
        self.assertEqual(passed, ["tests/test_x.py::test_a", "tests/test_x.py::test_c"])
        self.assertEqual(failed, ["tests/test_x.py::test_b", "tests/test_y.py"])
        self.assertEqual(errors[0], "tests/test_x.py::test_b - AssertionError: 1 != 2")

    def test_group1_does_not_push_back_failures_that_existed_at_the_start(self):
        failed = ["t.py::test_old", "t.py::test_new"]
        errors = ["t.py::test_old - KeyError: 'a'", "t.py::test_new - AssertionError"]
        keep, errs, pre = ms_adapter.split_preexisting(failed, errors, {"t.py::test_old", "u.py::test_x"})
        self.assertEqual((keep, errs, pre), (["t.py::test_new"], ["t.py::test_new - AssertionError"], ["t.py::test_old"]))
        self.assertEqual(ms_adapter.split_preexisting(failed, errors, set()), (failed, errors, []))
        keep, _, pre = ms_adapter.split_preexisting(["c.py::test_a", "d.py::test_b"], [], {"c.py"})
        self.assertEqual((keep, pre), (["d.py::test_b"], ["c.py::test_a"]), "始まりに集めで落ちたファイルの中の項目は前からの落ち")

    def test_parse_pyflakes_ignores_positions(self):
        out = ("./skutil/a.py:3: UndefinedName: undefined name 'foo'\n"
               "skutil/a.py:9: UndefinedLocal: local variable 'x' defined in enclosing scope on line 2 referenced "
               "before assignment\n"
               "skutil/b.py:1: syntax: invalid syntax\n")
        got = ms_adapter.parse_pyflakes(out)
        self.assertEqual(got, collections.Counter({
            ("skutil/a.py", "UndefinedName: undefined name 'foo'"): 1,
            ("skutil/a.py", "UndefinedLocal: local variable 'x' defined in enclosing scope on line N referenced "
                            "before assignment"): 1,
            ("skutil/b.py", "syntax: invalid syntax"): 1}))
        moved = ms_adapter.parse_pyflakes("skutil/a.py:30: UndefinedName: undefined name 'foo'\n")
        self.assertEqual(moved - got, collections.Counter(), "行が動いただけの検出は新しい検出にしない")

    def test_pyflakes_script_counts_only_undefined_names_and_syntax_errors(self):
        self.assertIn("KEEP = (messages.UndefinedName, messages.UndefinedExport, messages.UndefinedLocal)",
                      ms_adapter.PYFLAKES_SCRIPT, "fb_gate_probe.check_undefined と同じ組（P-038）")
        self.assertIn("def syntaxError", ms_adapter.PYFLAKES_SCRIPT)
        cfg = ms_run.load_config()
        self.assertEqual(cfg["ms"]["pyflakes"][-2:], ["-I", "-"], "スクリプトは標準入力で渡す")
        self.assertIn("pyflakes==4.1.0", cfg["ms"]["pyflakes"])

    def test_expand_changed_placeholders(self):
        changed = ["calc/eval.go", "calc/eval_test.go", "opc/zip.go", "README.md"]
        self.assertEqual(ms_adapter.expand(["go", "vet", "{changed_dirs:*.go}"], changed), ["go", "vet", "./calc", "./opc"])
        self.assertEqual(ms_adapter.expand(["go", "test", "{changed_dirs:*_test.go}"], changed), ["go", "test", "./calc"])
        self.assertEqual(ms_adapter.expand(["cat", "{changed:*.md}"], changed), ["cat", "README.md"])
        self.assertEqual(ms_adapter.expand(["go", "vet", "{changed_dirs:*.go}"], ["main.go"]), ["go", "vet", "."])
        self.assertIsNone(ms_adapter.expand(["go", "test", "{changed_dirs:*_test.go}"], ["README.md"]))
        self.assertEqual(ms_adapter.expand(["go", "build", "./..."], []), ["go", "build", "./..."])

    def test_root_paths_picks_single_paths_under_root(self):
        env = ["PATH=/go/bin:/usr/local/go/bin:/root/.local/bin", "GOPATH=/go", "GOCACHE=/root/.cache/go-build/",
               "XDG_CONFIG_HOME=/root/.config", "HOME=/root", "NOTE=/root/a b", "GOFLAGS=-buildvcs=false"]
        self.assertEqual(ms_run.root_paths(env), {"GOCACHE": "/root/.cache/go-build", "XDG_CONFIG_HOME": "/root/.config"})
        self.assertEqual(ms_run.root_paths(None), {})

    def test_test_file_globs(self):
        self.assertTrue(ms_adapter.is_test_file("skutil/tests/test_util.py"))
        self.assertTrue(ms_adapter.is_test_file("pkg/util_test.py"))
        self.assertFalse(ms_adapter.is_test_file("skutil/testing.py"))

    def test_stacked_items_are_read_one_by_one(self):
        ad = ms_adapter.MsAdapter("box", {"uid": 1000, "home": "/home/agent"}, tempfile.gettempdir())
        ad.base_failed = {"a.py::test_env"}
        out = ("PASSED a.py::test_ok\nFAILED a.py::test_env - scipy...\nFAILED a.py::test_broken - AssertionError\n"
               "PASSED a.py::test_other\n")
        ad._pytest = lambda files: (1, out)
        items = ["a.py::test_ok", "a.py::test_env", "a.py::test_broken", "a.py::test_removed", "gone.py::test_x"]
        r = ad._k_pytest_items(core.Check("u1:tests", "pytest", ["changed"], role="regression", items=items),
                               {"a.py": "x"})
        self.assertEqual(r.status, "fail")
        self.assertEqual((r.passed, r.failed), (["a.py::test_ok"], ["a.py::test_broken"]))
        self.assertIn("1 failing at the start of this step (not counted), 2 not found", r.summary)
        ad._pytest = lambda files: (1, "PASSED a.py::test_ok\nFAILED a.py::test_env - scipy...\n")
        reg = core.Check("u1:tests", "pytest", ["changed"], role="regression",
                         items=["a.py::test_ok", "a.py::test_env", "a.py::test_removed"])
        r = ad._k_pytest_items(reg, {"a.py": "x"})
        self.assertEqual(r.status, "pass", "前からの落ちと消えた項目だけなら通す")
        self.assertEqual(sorted(r.skipped), ["a.py::test_env", "a.py::test_removed"])
        self.assertEqual(core.check_status(reg, r), "pass", "制御核も skipped を除いて読む")
        ad._pytest = lambda files: (4, "ERROR: usage")
        r = ad._k_pytest_items(core.Check("u1:tests", "pytest", ["changed"], role="regression", items=["a.py::t"]),
                               {"a.py": "x"})
        self.assertEqual(r.status, "fail", "pytest 自体が走れなければ消えた扱いにしない")

    def test_adapter_kinds_cover_the_config(self):
        cfg = ms_run.load_config()
        kinds = {d["kind"] for task in DEV for d in cfg["ms"]["gate"][task]}
        self.assertTrue(kinds <= set(ms_adapter.MsAdapter.KINDS))
        self.assertTrue(ms_adapter.MsAdapter.protects)


if __name__ == "__main__":
    unittest.main()
