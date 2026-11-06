"""分解役・構造化役・外部の調べもの（Claude の 2 つ）の起こし方が、提案役（config/proposer.json）と同じ形であること
（v3 §15 の 11-05 その 2。L7 の前の残り）：使える道具そのものを絞り（--tools。allowedTools は許すだけで、ほかの道具は残る）、
人の設定を読まず（--restricted。この PC の人の既定の許しの方式 auto を受け継がない）、MCP の連携を持たず、思考の深さを旗で固める。
プロンプトをファイルで渡す分解役と構造化役は、その置き場（作業の場所の外）を --add-dir で読む根に足す。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import decompose  # noqa: E402
import spec  # noqa: E402


def load(name):
    return json.loads((ROOT / "config" / name).read_text(encoding="utf-8"))


def value(args, flag):
    return args[args.index(flag) + 1]


def envelope(text, model):
    """claude CLI の JSON の封筒（本文と、使われたモデルの modelUsage）。"""
    return json.dumps({"result": text, "usage": {"input_tokens": 1, "output_tokens": 1}, "modelUsage": {model: {"outputTokens": 1}}})


# (どこの設定か, 旗の取り出し方, 使える道具, 思考の深さ（各モデルの既定）)
ROLES = [("config/decompose.json", lambda: load("decompose.json")["extra_flags"], "Read,Grep,Glob", "high"),
         ("config/spec.json", lambda: load("spec.json")["extra_flags"], "Read", "high"),
         ("config/research.json の claude", lambda: load("research.json")["backends"]["claude"]["extra_flags"], "WebSearch,WebFetch", "medium"),
         ("config/research.json の claude-deep", lambda: load("research.json")["backends"]["claude-deep"]["extra_flags"],
          "WebSearch,WebFetch", "high")]


class Flags(unittest.TestCase):
    def test_each_role_narrows_tools_ignores_user_settings_and_pins_effort(self):
        for where, flags_of, tools, effort in ROLES:
            with self.subTest(where=where):
                flags = flags_of()
                self.assertEqual(value(flags, "--tools"), tools)
                self.assertEqual(value(flags, "--allowedTools"), tools)
                self.assertIn("--restricted", flags)
                self.assertIn("--strict-mcp-config", flags)
                self.assertEqual(value(flags, "--effort"), effort)
                self.assertTrue({"Bash", "PowerShell", "Edit", "Write"} <= set(value(flags, "--disallowedTools").split(",")))
                self.assertNotIn("--permission-mode", flags)   # --restricted の下では default
                self.assertNotIn("--dangerously-skip-permissions", flags)

    def test_refused_tools_do_not_overlap_the_tools_a_role_needs(self):
        for where, flags_of, tools, _ in ROLES:
            with self.subTest(where=where):
                flags = flags_of()
                self.assertFalse(set(tools.split(",")) & set(value(flags, "--disallowedTools").split(",")))

    def test_research_keeps_its_turn_caps(self):
        backends = load("research.json")["backends"]
        self.assertEqual((value(backends["claude"]["extra_flags"], "--max-turns"),
                          value(backends["claude-deep"]["extra_flags"], "--max-turns")), ("12", "40"))


class PromptFolder(unittest.TestCase):
    """プロンプトのファイルの置き場を --add-dir で渡す（--restricted の下では、読む道具は cwd と --add-dir の中だけ）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_decompose_adds_the_prompt_folder(self):
        for name in ("CFG", "ROOT", "LOG_DIR"):
            self.addCleanup(setattr, decompose, name, getattr(decompose, name))
        cfg = load("decompose.json")
        cfg["prompt_file"] = str(self.tmp / "prompt" / "prompt.md")
        decompose.CFG, decompose.ROOT, decompose.LOG_DIR = cfg, self.tmp / "repo", self.tmp / "log"
        calls = []

        def fake_run(args, ttl, label):
            calls.append(args)
            return 0, envelope("{}", cfg["model"]), ""

        with mock.patch.object(decompose, "run", fake_run), mock.patch.object(decompose, "resolve_cli", lambda c: c), \
                mock.patch.dict(decompose.TEL, {}, clear=False):
            decompose.call_claude("x")
        self.assertEqual(Path(value(calls[0], "--add-dir")), self.tmp / "prompt")
        self.assertIn("--restricted", calls[0])

    def test_spec_adds_the_prompt_folder(self):
        cfg = load("spec.json")
        prompt = self.tmp / "work" / "attempt_1" / "prompt.md"
        prompt.parent.mkdir(parents=True)
        prompt.write_text("x", encoding="utf-8")
        calls = []

        def fake_run(args, cwd, ttl, label):
            calls.append(args)
            return 0, envelope("x", cfg["model"]), ""

        with mock.patch.object(spec, "run", fake_run), mock.patch.object(spec, "resolve_cli", lambda c: [c]):
            spec.call_cli(cfg, prompt)
        self.assertEqual(Path(value(calls[0], "--add-dir")), prompt.parent)
        self.assertIn("--restricted", calls[0])


if __name__ == "__main__":
    unittest.main()
