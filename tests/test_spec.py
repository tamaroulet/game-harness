"""構造化役（harness/spec.py、docs/design/spec_pipeline.md §2・§8）。

    python -m unittest discover -s tests -v

Claude CLI は偽物（proc.run の差し替え。応答を順に返す）。GitHub にも LLM にも触れない。
GDD と合格する spec は tests/test_gdd_check.py の土台を使う。
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "tests"))

import gdd_check  # noqa: E402
import project  # noqa: E402
import spec  # noqa: E402
import test_gdd_check as fx  # noqa: E402


def body(doc):
    """メタデータの 3 行を落とした本文（LLM が返す形）。"""
    return doc.split("\n", 3)[3]


def response(spec_doc=None, q_doc=None):
    s = body(spec_doc if spec_doc is not None else fx.spec())
    q = body(q_doc if q_doc is not None else fx.questions())
    return f"<<<SPEC_MD\n{s.rstrip()}\nSPEC_MD>>>\n<<<QUESTIONS_MD\n{q.rstrip()}\nQUESTIONS_MD>>>\n"


class FakeCli:
    def __init__(self, texts, rc=0, envelope=None, models=None):
        self.texts = list(texts)
        self.rc, self.envelope, self.models = rc, envelope, models
        self.calls, self.prompts = [], []

    def __call__(self, args, cwd, ttl, label, env=None):
        self.calls.append(args)
        if args[:2] == ["git", "rev-parse"]:
            return 0, "h" * 40 + "\n", ""
        if "--version" in args:
            return 0, "2.1.258 (Claude Code)\n", ""
        prompt_path = args[2].split(" ", 1)[0]
        self.prompts.append(Path(prompt_path).read_text(encoding="utf-8"))
        if self.rc != 0:
            # 異常終了でも、読める形の封筒と合格する本文を返す（終了コードを見ずに本文を使う実装を検出するため）
            return self.rc, json.dumps({"result": response(), "is_error": True}), "boom"
        if self.envelope is not None:
            return 0, self.envelope, ""
        doc = {"result": self.texts.pop(0), "num_turns": 1, "total_cost_usd": 0.01,
               "usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0}}
        if self.models is not None:
            doc["modelUsage"] = {m: {} for m in self.models}
        return 0, json.dumps(doc), ""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.gdd = self.tmp / "source.md"
        self.gdd.write_bytes(fx.GDD.encode("utf-8"))
        self.out = self.tmp / "docs" / "spec"
        self.work = self.tmp / "work"
        self.tel = self.tmp / "tel.json"
        q = mock.patch("builtins.print")
        q.start()
        self.addCleanup(q.stop)

    def main(self, fake, *extra):
        with mock.patch.object(spec, "run", side_effect=fake), \
                mock.patch.object(spec, "resolve_cli", return_value=["claude"]):
            return spec.main(["--project", "falling-blocks", "--gdd", str(self.gdd), "--out-dir", str(self.out),
                              "--work-dir", str(self.work), "--telemetry", str(self.tel)] + list(extra))

    def telemetry(self):
        return json.loads(self.tel.read_text(encoding="utf-8"))


class SuccessTests(Base):
    def test_first_attempt_passes_and_writes_files_with_computed_metadata(self):
        fake = FakeCli([response()], models=["claude-opus-5"])
        self.assertEqual(self.main(fake), 0)
        written = (self.out / "spec.md").read_bytes().decode("utf-8")
        self.assertEqual(written, fx.spec())                       # メタデータは spec.py が GDD から計算
        # メタデータの直後の空行は詰める（見出しの前の空行は書式上どちらでもよい）
        self.assertEqual((self.out / "questions.md").read_bytes().decode("utf-8"),
                         fx.questions().replace("-->\n\n## 質問", "-->\n## 質問"))
        t = self.telemetry()
        self.assertEqual((t["exit_code"], len(t["attempts"]), t["result"]["mergeable"]), (0, 1, True))
        self.assertEqual(t["cli"], {"name": "claude", "version": "2.1.258 (Claude Code)", "model_requested": "claude-opus-5"})
        self.assertEqual(t["attempts"][0]["models_used"], ["claude-opus-5"])
        self.assertEqual(t["harness_sha"], "h" * 40)
        self.assertEqual(t["gdd"]["sha256"], spec.sha256(fx.GDD))
        for name in ("prompt.md", "response.txt", "check.json"):
            self.assertTrue((self.work / "attempt_1" / name).exists(), name)
        self.assertTrue((self.work / "summary.json").exists())

    def test_model_flag_and_readonly_tools_are_passed(self):
        fake = FakeCli([response()])
        self.main(fake)
        args = next(c for c in fake.calls if "-p" in c)
        self.assertEqual(args[args.index("--model") + 1], "claude-opus-5")
        self.assertEqual(args[args.index("--allowedTools") + 1], "Read")
        self.assertIn("--output-format", args)

    def test_metadata_written_by_the_llm_is_replaced(self):
        wrong = "<!-- project: x -->\n<!-- gdd-version: 9 -->\n<!-- gdd-sha256: " + "0" * 64 + " -->\n"
        text = response().replace("<<<SPEC_MD\n", "<<<SPEC_MD\n" + wrong)
        self.assertEqual(self.main(FakeCli([text])), 0)
        self.assertEqual((self.out / "spec.md").read_bytes().decode("utf-8"), fx.spec())

    def test_gdd_shortfall_is_written_but_not_mergeable_and_not_retried(self):
        q = fx.questions(["| Q-01 | ソフトドロップは何倍か | L7 | GDD に値が無い |"])
        fake = FakeCli([response(q_doc=q)])
        self.assertEqual(self.main(fake), 0)
        t = self.telemetry()
        self.assertEqual((len(t["attempts"]), t["result"]["mergeable"], t["result"]["questions"]), (1, False, 1))
        self.assertTrue((self.out / "questions.md").exists())


class RetryTests(Base):
    def test_ng_then_pass_feeds_back_problems_and_previous_output(self):
        bad = response(fx.spec(lp=["| LP-01 | PR-99 を使う | L6 |"]))
        fake = FakeCli([bad, response()])
        self.assertEqual(self.main(fake), 0)
        self.assertEqual(len(fake.prompts), 2)
        self.assertNotIn("前回の出力は検査に不合格", fake.prompts[0])
        self.assertIn("実在しない ID PR-99", fake.prompts[1])
        self.assertIn("PR-99 を使う", fake.prompts[1])          # 前回の出力も渡す
        t = self.telemetry()
        self.assertEqual([a["passed"] for a in t["attempts"]], [False, True])
        self.assertEqual(t["attempts"][0]["problems_count"], 1)

    def test_all_attempts_fail_writes_nothing(self):
        bad = response(fx.spec(lp=["| LP-01 | PR-99 を使う | L6 |"]))
        fake = FakeCli([bad] * 3)
        self.assertEqual(self.main(fake), 1)
        self.assertEqual(len(fake.prompts), 3)
        self.assertFalse((self.out / "spec.md").exists())
        self.assertFalse((self.out / "questions.md").exists())
        self.assertEqual(self.telemetry()["result"], {"passed": False})

    def test_missing_delimiters_is_a_failed_attempt(self):
        fake = FakeCli(["説明だけの応答", response()])
        self.assertEqual(self.main(fake), 0)
        self.assertIn("区切り", fake.prompts[1])


class PromptTests(Base):
    def test_prompt_contains_numbered_gdd_terms_and_previous_spec(self):
        prev = self.tmp / "prev.md"
        prev.write_bytes(fx.spec(lp=["| LP-01 | 前の版の行 | L6 |", "| LP-02 | x | L7 |"]).encode("utf-8"))
        fake = FakeCli([response()])
        self.main(fake, "--previous-spec", str(prev))
        p = fake.prompts[0]
        self.assertIn("L6: - 幅 10 × 高さ 20。", p)
        self.assertIn("ハードドロップ: ハードドロップ、瞬間落下", p)
        self.assertIn("前の版の行", p)
        self.assertIn("最大番号より大きく", p)
        self.assertNotIn("$", p.replace("$gdd", ""))   # 置き換え漏れが無い


class EnvTests(Base):
    def test_cli_failure_is_env_and_writes_nothing(self):
        self.assertEqual(self.main(FakeCli([], rc=1)), 2)
        self.assertFalse(self.out.exists())

    def test_unreadable_envelope_is_env(self):
        self.assertEqual(self.main(FakeCli([], envelope="not json")), 2)

    def test_other_model_is_env(self):
        self.assertEqual(self.main(FakeCli([response()], models=["claude-haiku-4-5-20251001"])), 2)
        self.assertFalse(self.out.exists())

    def test_gdd_without_metadata_or_other_project_is_env(self):
        self.gdd.write_bytes(fx.GDD.split("\n", 2)[2].encode("utf-8"))
        self.assertEqual(self.main(FakeCli([response()])), 2)
        self.gdd.write_bytes(fx.GDD.replace("project: falling-blocks", "project: unity-2d").encode("utf-8"))
        self.assertEqual(self.main(FakeCli([response()])), 2)


class ConfigTests(unittest.TestCase):
    def test_model_is_pinned(self):
        cfg = project.config("spec")
        self.assertTrue(cfg["model"])
        self.assertGreaterEqual(cfg["max_attempts"], 1)
        self.assertTrue((project.ROOT / cfg["prompt_template_file"]).exists())


if __name__ == "__main__":
    unittest.main()
