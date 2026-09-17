"""エンジン別・言語別のアダプタ（Step 2）。

    python -m unittest discover -s tests -v

- コア（pipeline / scheduler / project / proc / exitcode）にエンジン・言語固有の語が無いこと
- アダプタの選択は、知らない名前・足りない関数で止まること（既定値に落とさない）
- 結果ファイルの読み取りは、実物（unity-2d の自己検査で出た XML と TRX）で確かめる
"""
import json
import re
import sys
import tempfile
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "harness"
sys.path.insert(0, str(HARNESS))

import adapters  # noqa: E402
from adapters import dotnet, unity  # noqa: E402

FIX = ROOT / "tests" / "fixtures"

CORE_FILES = ["pipeline.py", "scheduler.py", "project.py", "proc.py", "exitcode.py", "oracle.py",
              "telemetry.py"]

# コアに現れてはいけない語。プロジェクト ID の "unity-2d" は使用例として許す
#（\bUnity\b は大文字始まりだけ、\bunity_ はキー名だけを捕まえる）。
FORBIDDEN_IN_CORE = [
    r"\bUnity\b", r"\bunity_", r"\.meta\b", r"batchmode", r"EditMode", r"NUnit",
    r"UnityEngine", r"MonoBehaviour", r"Mathf", r"MetaPoint", r"\bdotnet\b",
    r"\bTRX\b", r"\btrx\b", r"\.cs\b", r"Game\.Core", r"asmdef", r"\bguid",
]


class CoreIsEngineNeutralTests(unittest.TestCase):
    def test_core_sources_have_no_engine_or_language_words(self):
        hits = []
        for name in CORE_FILES:
            for n, line in enumerate((HARNESS / name).read_text(encoding="utf-8").splitlines(), 1):
                for pat in FORBIDDEN_IN_CORE:
                    if re.search(pat, line):
                        hits.append(f"{name}:{n} {pat}: {line.strip()[:80]}")
        self.assertEqual(hits, [], "コアに固有の語が残っています")

    def test_forbidden_word_list_actually_catches_old_code(self):
        """検査そのものが空振りしていないこと。移設前の典型的な行に当たるか。"""
        samples = ['unity = c.cfg["unity_exe"]', 'if path.endswith(".meta"):',
                   'run(["dotnet", "test"])', 'trx = c.out / f"{tag}.trx"',
                   'found = list(c.sandbox.rglob(f"*{t}*.cs"))', 'print("[4] Unity 受入")']
        for s in samples:
            with self.subTest(s):
                self.assertTrue(any(re.search(p, s) for p in FORBIDDEN_IN_CORE), s)


class RegistryTests(unittest.TestCase):
    def test_known_adapters_load_and_satisfy_the_interface(self):
        for kind, names in adapters.KNOWN.items():
            for name in names:
                with self.subTest(f"{kind}/{name}"):
                    mod = adapters.load(kind, name)
                    for attr in adapters.REQUIRED[kind]:
                        self.assertTrue(hasattr(mod, attr), attr)

    def test_unknown_names_are_refused(self):
        for kind, name in (("engine", "godot"), ("fast", "pytest"), ("physics", "unity")):
            with self.subTest(f"{kind}/{name}"), self.assertRaises(adapters.AdapterError):
                adapters.load(kind, name)

    def test_adapter_missing_a_function_is_refused(self):
        broken = types.ModuleType("adapters.broken")
        broken.LABEL = "Broken"
        with mock.patch.dict(sys.modules, {"adapters.broken": broken}), \
                mock.patch.dict(adapters.KNOWN["engine"], {"broken": "adapters.broken"}):
            with self.assertRaises(adapters.AdapterError) as ctx:
                adapters.load("engine", "broken")
        self.assertIn("run_tests", str(ctx.exception))


class UnityAdapterTests(unittest.TestCase):
    def test_parse_real_nunit3_results(self):
        """unity-2d の自己検査 [C]（非開示投入）の実物: Passed 176 / Skipped 20 / Failed 1（対照群）。"""
        results = unity.parse_results(FIX / "unity_nunit3_results.xml")
        counts = Counter(results.values())
        self.assertEqual(counts[unity.PASSED], 176)
        self.assertEqual(counts[unity.FAILED], 1)
        self.assertEqual(sum(counts[s] for s in unity.SKIPPED), 20)
        failed = [n for n, o in results.items() if o == unity.FAILED]
        self.assertTrue(all("control_must_fail" in n for n in failed), failed)

    def test_companion_rules(self):
        self.assertTrue(unity.is_companion("Game/Assets/Core/X.cs.meta"))
        self.assertFalse(unity.is_companion("Game/Assets/Core/X.cs"))
        self.assertEqual(unity.companions("A/B.cs"), ["A/B.cs.meta"])

    def test_carry_companion_protects_guid(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src, dst = d / "src.meta", d / "dst.meta"
            self.assertEqual(unity.carry_companion(src, dst)[0], "skip", "サンドボックスに無い")
            src.write_text("fileFormatVersion: 2\nguid: aaaa\n", encoding="utf-8")
            self.assertEqual(unity.carry_companion(src, dst)[0], "copy", "本体に無い（新規）")
            dst.write_text("fileFormatVersion: 2\nguid: aaaa\n", encoding="utf-8")
            self.assertEqual(unity.carry_companion(src, dst)[0], "skip", "同じ GUID は上書きしない")
            dst.write_text("fileFormatVersion: 2\nguid: bbbb\n", encoding="utf-8")
            action, why = unity.carry_companion(src, dst)
            self.assertEqual(action, "abort", "GUID が違えば止める")
            self.assertIn("GUID", why)

    def test_project_dir_requires_its_key(self):
        c = types.SimpleNamespace(cfg={"project": {}}, sandbox=Path("X"))
        with self.assertRaises(SystemExit) as ctx:
            unity.project_dir(c)
        self.assertIn("ABORT", str(ctx.exception.code))


class DotnetAdapterTests(unittest.TestCase):
    def test_parse_real_trx_joins_class_and_method(self):
        """unity-2d の自己検査の実物: 80 件すべて Passed。名前は className.methodName。"""
        results, counters = dotnet.parse_results(FIX / "dotnet_results.trx")
        self.assertEqual(counters["total"], 80)
        self.assertEqual(counters["passed"], 80)
        self.assertEqual(len(results), 80)
        self.assertTrue(all(o == dotnet.PASSED for o in results.values()))
        self.assertTrue(any(n.split(".")[-2] == "BossChargeStateTests" for n in results),
                        "クラス名で照合できること（メソッド名だけだと required_tests が見つからない）")

    def test_test_path_regex(self):
        for p in ("tests/Core.Tests/XTests.cs", "Game/Assets/Tests/Y.cs", "tests/golden/golden_a.json",
                  "Game/Assets/Core/ZTest.cs"):
            with self.subTest(p):
                self.assertRegex(p, dotnet.TEST_PATH_RE)
        for p in ("Game/Assets/Core/BossChargeState.cs", "Game/Assets/Features/Boss/View.cs"):
            with self.subTest(p):
                self.assertNotRegex(p, dotnet.TEST_PATH_RE)

    def test_run_tests_disables_msbuild_node_reuse(self):
        """/nr:false が無いと MSBuild のワーカーが常駐し、.dll を掴んで次のビルドを落としうる。"""
        with tempfile.TemporaryDirectory() as d:
            c = types.SimpleNamespace(out=Path(d), sandbox=Path(d), unit={"fast_test_project": "p.csproj"},
                                      ttl={"fast_tests": 1}, metrics={})
            with mock.patch.object(dotnet, "run", return_value=(0, "", "")) as run:
                dotnet.run_tests(c, "t")
        args = run.call_args[0][0]
        self.assertEqual(args[:2], ["dotnet", "test"])
        self.assertIn("/nr:false", args)

    def test_stub_and_globs(self):
        self.assertIn("namespace", dotnet.STUB_SOURCE)
        self.assertEqual(dotnet.test_file_glob("BossChargeStateTests"), "*BossChargeStateTests*.cs")
        self.assertEqual(dotnet.build_output_globs("g.json"), ["tests/**/bin/**/g.json"])


class ProjectAdaptersKeyTests(unittest.TestCase):
    def test_malformed_adapters_in_project_json_are_refused(self):
        import project
        real = project._read
        for bad in ("unity", {"engine": "unity"}, {"engine": 1, "fast": "dotnet"}):
            def fake(path, bad=bad):
                d = real(path)
                if Path(path).name == "project.json":
                    d["adapters"] = bad
                return d
            with self.subTest(bad), mock.patch.object(project, "_read", side_effect=fake):
                with self.assertRaises(project.ProjectError):
                    project.load("unity-2d")


class PipelineSelectsAdaptersTests(unittest.TestCase):
    def _ctx(self, adapters_cfg):
        import pipeline
        with tempfile.TemporaryDirectory() as d:
            unit = Path(d) / "u.json"
            unit.write_text(json.dumps({"id": "u", "whitelist": ["a"]}), encoding="utf-8")
            cfg = {"paths": {"repo": d, "out_dir": d, "sandbox": d},
                   "ttl_seconds": {}, "control_groups": {"must_pass": "p", "must_fail": "f"},
                   "adapters": adapters_cfg, "project": {}}
            return pipeline.Ctx(cfg, unit)

    def test_pipeline_uses_configured_adapters(self):
        c = self._ctx({"engine": "unity", "fast": "dotnet"})
        self.assertIs(c.engine, unity)
        self.assertIs(c.fast, dotnet)

    def test_pipeline_aborts_on_unknown_or_missing_adapter(self):
        for cfg in ({"engine": "godot", "fast": "dotnet"}, {"fast": "dotnet"}):
            with self.subTest(cfg), self.assertRaises(SystemExit) as ctx:
                self._ctx(cfg)
            self.assertIn("ABORT", str(ctx.exception.code))


if __name__ == "__main__":
    unittest.main()
