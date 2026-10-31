"""実装役の呼び出しの禁止の列（受信箱・生ログ・非公開オラクルへの Read/Grep/Glob と Bash）の検査。proc.run を差し替え、外部の CLI は起こさない。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline  # noqa: E402
import machine  # noqa: E402
import oracle_deny  # noqa: E402

CFG = hline.load_config()


class Rules(unittest.TestCase):
    def test_the_config_has_the_oracles_place(self):
        # この PC の local の下の oracles。区切りは OS のもの（C:\... の文字列を直に書くと、Linux では ${local}/oracles の / と食い違う）
        self.assertEqual(Path(CFG["oracles"]), machine.path("local") / "oracles")

    def test_windows_paths_become_double_slash_drive_rules(self):
        self.assertEqual(oracle_deny.rule_path("C:\\src\\.local\\inbox"), "//c/src/.local/inbox/**")
        self.assertEqual(oracle_deny.rule_path("D:\\x\\y\\"), "//d/x/y/**")

    def test_three_tools_for_each_of_the_three_places_and_bash(self):
        rules = oracle_deny.deny_rules(CFG)
        want = [f"{t}(//c/src/.local/{p}/**)" for p in ("inbox", "out/hline", "oracles") for t in ("Read", "Grep", "Glob")] + ["Bash"]
        self.assertEqual(rules, want)
        self.assertEqual(len(rules), 10)
        self.assertEqual(oracle_deny.deny_list(CFG), ",".join(want))
        self.assertEqual(oracle_deny.deny_flags(CFG), ["--disallowedTools", ",".join(want)])

    def test_changing_the_config_changes_the_list(self):
        other = dict(CFG, inbox="E:\\a\\inbox", out="E:\\a\\out", oracles="E:\\a\\oracles")
        rules = oracle_deny.deny_rules(other)
        self.assertIn("Read(//e/a/oracles/**)", rules)
        self.assertIn("Glob(//e/a/inbox/**)", rules)
        self.assertNotEqual(rules, oracle_deny.deny_rules(CFG))

    def test_a_missing_place_is_an_error(self):
        bad = {k: v for k, v in CFG.items() if k != "oracles"}
        with self.assertRaises(ValueError):
            oracle_deny.deny_rules(bad)

    def test_with_deny_leaves_the_config_alone(self):
        before = json.dumps(CFG["implementer"], sort_keys=True)
        got = oracle_deny.with_deny(CFG["implementer"], CFG)
        self.assertEqual(json.dumps(CFG["implementer"], sort_keys=True), before)
        self.assertEqual(got["extra_flags"][-2:], oracle_deny.deny_flags(CFG))


class Call(unittest.TestCase):
    def call_args(self, cfg):
        out = json.dumps({"result": "x", "modelUsage": {CFG["implementer"]["model"]: {}}, "num_turns": 1})
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(hline.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(hline.proc, "run", return_value=(0, out, "")) as run:
            hline.implement(cfg, Path(d), "# 題", None, Path(d) / "log")
        return run.call_args.args[0]

    def test_the_implementer_call_carries_the_deny_list(self):
        args = self.call_args(CFG)
        self.assertEqual(args[args.index("--disallowedTools") + 1], oracle_deny.deny_list(CFG))
        self.assertIn("Bash", args[args.index("--disallowedTools") + 1].split(","))

    def test_the_call_follows_the_config(self):
        other = dict(CFG, oracles="E:\\other\\oracles")
        args = self.call_args(other)
        self.assertIn("Read(//e/other/oracles/**)", args[args.index("--disallowedTools") + 1].split(","))


if __name__ == "__main__":
    unittest.main()
