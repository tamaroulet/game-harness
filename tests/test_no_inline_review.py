"""インラインの LLM レビュー（監査役）を撤去したことの検査（S2-3）。

    python -m unittest tests.test_no_inline_review -v

合格条件 1〜4 に 1 対 1 で対応する。判定は決定的な検査（テストの終了コード・編集境界・規模）だけで決まり、
LLM の応答を入力にしない。ファイルの走査は、リポジトリの根からの明示のパスの集合で行う（.git・experiments・reports は見ない）。
"""
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline  # noqa: E402
import scheduler  # noqa: E402

CFG = hline.load_config()

REMOVED_FILES = ("harness/audit.py", "config/audit.json", "tests/test_audit_verdict.py")

# 監査の設定・プロンプトを読む経路の目印。
READ_PATTERNS = ('project.config("audit")', 'cfg["audit"]', 'cfg["audit_dir"]', "audit_dir",
                 "verdict_prompt", "system_prompt", "OPENAI_API_KEY", "Audit-Verdict", "audit.py", "audit.json")

# 走査から外すもの。propaudit は性質（property）の静的監査で LLM を呼ばない決定的な検査。名前に audit が
# 付くだけで、インラインの LLM レビューではない。
ALLOWED_AUDIT_NAMES = ("harness/propaudit.py",)


def sources():
    """harness/*.py と config/*.json（ALLOWED_AUDIT_NAMES を除く）。"""
    files = sorted(ROOT.glob("harness/*.py")) + sorted(ROOT.glob("config/*.json"))
    return [p for p in files if p.relative_to(ROOT).as_posix() not in ALLOWED_AUDIT_NAMES]


class NoInlineReview(unittest.TestCase):
    def test_gate_paths_contain_no_model_invocation(self):
        """条件 1: H ラインの門は unittest の終了コードだけ。スケジューラの Issue の処理に監査の段が無い。"""
        self.assertEqual(CFG["gate_command"][:2], ["python", "-m"])
        self.assertIn(CFG["gate_command"][2], ("unittest", "harness.fastsuite"), "走らせるのはテストの実行器だけ")
        gate_src = inspect.getsource(hline.gate)
        for word in ("run_agent", "implement(", "claude", "agy", "OPENAI"):
            self.assertNotIn(word, gate_src)
        process_src = inspect.getsource(scheduler.Scheduler.process)
        self.assertNotIn('"audit"', process_src)
        self.assertNotIn("verdict", process_src)
        self.assertEqual([n for n in ("decompose", "pipeline") if f'"{n}"' in process_src],
                         ["decompose", "pipeline"], "分解役と実装役の呼び出しは残る")

    def test_no_llm_review_module_or_config_remains(self):
        """条件 2: 監査のファイル・設定・プロンプト・それを読む経路がリポジトリに残っていない。"""
        for rel in REMOVED_FILES:
            self.assertFalse((ROOT / rel).exists(), rel)
        for path in sources():
            text = path.read_text(encoding="utf-8")
            for pat in READ_PATTERNS:
                self.assertNotIn(pat, text, f"{path.relative_to(ROOT).as_posix()} に {pat}")
        cfg = json.loads((ROOT / "config/scheduler.json").read_text(encoding="utf-8"))
        self.assertNotIn("audit", cfg)
        self.assertNotIn("audit", cfg["commands"])
        self.assertNotIn("audit", cfg["ttl_seconds"])
        self.assertEqual(sorted(cfg["commands"]), ["decompose", "pipeline", "playtest", "spec"])

    def test_scheduler_verdict_path_is_gone(self):
        """条件 2（スケジューラ側）: 判定の経路の名前・trailer・引数が無い。"""
        for name in ("VERDICT_ORDER", "VERDICT_RANK", "VERDICT_SKIPPED", "AUDIT_LABEL"):
            self.assertFalse(hasattr(scheduler, name), name)
        for name in ("audit", "worst_verdict", "audit_dir"):
            self.assertFalse(hasattr(scheduler.Scheduler, name), name)
        self.assertNotIn("Audit-Verdict", scheduler.MERGE_TRAILERS)
        self.assertEqual(list(inspect.signature(scheduler.Scheduler.merge_message).parameters),
                         ["self", "n", "contract_sha"])
        self.assertEqual(list(inspect.signature(scheduler.Scheduler.integrated_report).parameters),
                         ["self", "n", "integ", "rec"])
        self.assertNotIn("audit_dir", scheduler.PROJECT_KEYS)

    def test_verdict_is_determined_without_any_llm_response(self):
        """条件 3: 門の判定は終了コードだけで決まる。同じ入力で 2 回呼んで同じ結果。出力の文面・鍵・ネットワークに依らない。"""
        for code, want in ((0, True), (1, False)):
            seen = []
            for env, out in (({}, "LGTM"), ({"OPENAI_API_KEY": "k"}, "REJECT: concern")):
                with self.subTest(code=code, env=env), \
                        mock.patch.dict(os.environ, env), \
                        mock.patch("socket.create_connection", side_effect=AssertionError("network")), \
                        mock.patch.object(hline.proc, "run", return_value=(code, out, "")):
                    seen.append(hline.gate(CFG, Path("."), ["harness/x.py"])[0])
            self.assertEqual(seen, [want, want])

    def test_tests_perform_no_push_no_pr_no_model_call(self):
        """条件 4: 門とマージ本文の組み立てで、push・PR・モデル・ネットワークの呼び出しが起きない。"""
        boom = AssertionError("外に出た")
        fake = SimpleNamespace(run_id="r1", harness_sha="h1", integration_branch=lambda: "integration/mvp")
        with mock.patch("subprocess.run", side_effect=boom) as sub_run, \
                mock.patch("subprocess.Popen", side_effect=boom) as popen, \
                mock.patch("urllib.request.urlopen", side_effect=boom) as urlopen, \
                mock.patch("socket.create_connection", side_effect=boom), \
                mock.patch.object(hline.proc, "run", return_value=(0, "ok", "")) as run:
            ok, _ = hline.gate(CFG, Path("."), ["harness/x.py"])
            message = scheduler.Scheduler.merge_message(fake, 5, "c1")
        self.assertTrue(ok)
        for m in (sub_run, popen, urlopen):
            m.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], CFG["gate_command"], "走らせたのはテストだけ")
        for word in ("push", "gh", "claude", "agy"):
            self.assertNotIn(word, run.call_args.args[0])
        self.assertIn("Gate-Result: PASSED", message)
        self.assertNotIn("Audit-Verdict", message)


if __name__ == "__main__":
    unittest.main()
