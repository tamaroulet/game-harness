"""H ライン：作業ツリーの根の AGENTS.md を実装役への指示に入れる（hline_prompt.agents_md_section）の検査。
作業ツリーは一時ディレクトリの偽物。実装役の CLI は呼ばず、host.proc.run の入力だけを捕まえる。"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_prompt  # noqa: E402

WHAT = "# T-1\n\nマイルストーン: M-1\nタスク: T-1\n\n## What\n本文\n"
LIMIT = 50


def cfg(limit=LIMIT):
    out = {"implementer": {"cli": "x", "budget": {"max_turns": 5}}, "ttl_seconds": {"implementer": 5}}
    if limit is not None:
        out["agents_md"] = {"max_chars": limit}
    return out


def run_implement(wt, config):
    seen = []
    host = types.SimpleNamespace(
        symbolmap=types.SimpleNamespace(prompt_text=lambda *a, **k: ""),
        proc=types.SimpleNamespace(run=lambda *a, **k: seen.append(k["input"]) or (0, "", ""), resolve_cli=lambda cli: cli),
        build_prompt=hline_prompt.build_prompt)
    with mock.patch.object(hline_prompt.oracle_deny, "with_deny", lambda agent, c: agent), \
            mock.patch.object(hline_prompt.impacted, "scoped_agent", lambda agent: agent), \
            mock.patch.object(hline_prompt.impacted, "prompt_note", lambda: ""), \
            mock.patch.object(hline_prompt, "capped_args", lambda agent, cli, cap: [cli]), \
            mock.patch.object(hline_prompt, "outcome", lambda agent, out, cap: ([], None, None, None)):
        hline_prompt.implement(host, config, wt, WHAT, None, Path(wt) / "log.txt")
    return seen[0]


class AgentsMdTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def write(self, text):
        (Path(self.root) / "AGENTS.md").write_text(text, encoding="utf-8")

    def test_content_before_what_separator(self):
        self.write("約束その一\n")
        prompt = run_implement(self.root, cfg())
        head, body = prompt.split("\n---\n", 1)
        self.assertIn("約束その一", head)
        self.assertNotIn("約束その一", body)
        self.assertIn("本文", body)

    def test_truncated_over_limit_with_note(self):
        self.write("あ" * 30 + "い" * 100)
        prompt = run_implement(self.root, cfg())
        head = prompt.split("\n---\n")[0]
        self.assertIn("あ" * 30 + "い" * (LIMIT - 30), head)
        self.assertNotIn("い" * (LIMIT - 29), head)
        self.assertTrue(any("切り詰め" in line for line in head.splitlines()))

    def test_within_limit_has_no_note(self):
        self.write("短い")
        self.assertNotIn("切り詰め", run_implement(self.root, cfg()))

    def test_missing_file_gives_no_section(self):
        self.assertEqual(hline_prompt.agents_md_section(cfg(), self.root), "")
        prompt = run_implement(self.root, cfg())
        self.assertNotIn("AGENTS.md", prompt)
        self.assertEqual(prompt, hline_prompt.build_prompt(WHAT, None, None))

    def test_no_limit_in_config_gives_no_section(self):
        self.write("約束")
        self.assertEqual(hline_prompt.agents_md_section(cfg(None), self.root), "")


class RepoAgentsMdTest(unittest.TestCase):
    def test_root_agents_md_within_limit_and_mentions_rules(self):
        limit = json.loads((ROOT / "config" / "hline.json").read_text(encoding="utf-8"))["agents_md"]["max_chars"]
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text), limit)
        for word in ("exitcode.normalized", "host", "protected_paths"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
