"""agy の呼び出しが途中で切れた（result の status が ERROR、error が interrupted）ときの扱い（v3 §15 の 11-12）。
実装役が終えたのではないので、環境の異常（Infra）にする。A（bench_solo）は段を無効（REQ-22）にして走り直し、B は What を呼び直す。
ほかの ERROR（実装役の側の失敗）は、今までどおり試行として返す。箱と CLI は呼ばない（in_box を差し替える）。"""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import bench_solo  # noqa: E402
import hline_agy  # noqa: E402
from hline_base import Infra  # noqa: E402

MODEL = "model-under-test"
AGENT = {"cli": "agy", "model": MODEL, "model_name": MODEL}


def ev(name, body):
    return json.dumps({"event": name, name: body})


def stream(status="SUCCESS", error=None):
    step = {"step_index": 0, "step_type": "tool_call", "state": "DONE", "tool_name": "view_file",
            "usage": {"input_tokens": 100, "output_tokens": 10, "thinking_tokens": 2, "cache_read_tokens": 50}}
    result = {"status": status, "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}}
    if error:
        result["error"] = error
    return "\n".join([ev("init", {"model": MODEL, "conversation_id": "c1"}), ev("step_update", step), ev("result", result)]) + "\n"


INTERRUPTED = (1, stream("ERROR", hline_agy.INTERRUPTED), "error: interrupted\n")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class Implement(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.log = self.root / "items" / "implementer-1.log"
        self.log.parent.mkdir(parents=True)
        self.cfg = {"implementer": AGENT, "box": {"package": "pkg"}, "ttl_seconds": {"implementer": 60}}
        self.host = types.SimpleNamespace(box_dropped=None)

    def implement(self, reply):
        with patch.object(hline_agy, "in_box", return_value=reply):
            return hline_agy.implement(self.host, self.cfg, self.root, "頼み", self.log)

    def test_an_interrupted_call_is_infra_and_its_log_stays(self):
        with self.assertRaises(Infra) as cm:
            self.implement(INTERRUPTED)
        self.assertIn("途中で切れ", str(cm.exception))
        self.assertIn(hline_agy.INTERRUPTED, str(cm.exception))
        head = json.loads(self.log.read_text(encoding="utf-8").split(hline_agy.LOG_SPLIT, 1)[0])
        self.assertEqual(head["usage"]["input_tokens"], 100)   # 切れた呼び出しの利用量も記録に残る

    def test_another_error_result_is_still_an_attempt(self):
        code, models, usage, cutoff, turns = self.implement((1, stream("ERROR", "the model returned an invalid reply"), ""))
        self.assertEqual((code, models, cutoff, turns), (1, [MODEL], None, 1))

    def test_a_successful_call_is_unchanged(self):
        code, models, usage, cutoff, turns = self.implement((0, stream(), ""))
        self.assertEqual((code, models, cutoff, turns), (0, [MODEL], None, 1))


class SoloStage(unittest.TestCase):
    """A の段：切れた呼び出しは無効（REQ-22）になり、run_step が走り直す（変更の無い木を採点して、次の段へ積まない）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo, self.out = self.root / "repo", self.root / "out"
        (self.repo / "pkg").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "start")
        self.start = git(self.repo, "rev-parse", "HEAD")
        spec = self.root / "spec.md"
        spec.write_text("# 仕様\n", encoding="utf-8")
        self.ctx = types.SimpleNamespace(out=self.out, base="B07", tip=self.start, repo=self.repo, agent=AGENT, pkg="pkg", k=1,
                                         what="# 題\n", spec=spec)
        for name, value in (("settings", {}), ("require", "sha256:image"), ("check", []), ("agy_version", "v")):
            p = patch.object(bench_solo.box, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_an_interrupted_call_makes_the_stage_invalid_with_its_cost(self):
        with patch.object(hline_agy, "in_box", return_value=INTERRUPTED):
            run = bench_solo.solo_stage(self.ctx, "t-s1", self.start, "頼み")
        self.assertEqual((run["invalid"], run["head"], run["stop"], run["tries"]), ("REQ-22", None, None, 1))
        self.assertIn("途中で切れ", run["reason"])
        self.assertEqual(run["tokens"]["total"], 160)   # 入力 100・キャッシュの読み 50・出力 10
        saved = bench_run.read_json(bench_run.run_dir(self.out, "B07", "t-s1") / "factory" / "run.json", {})
        self.assertEqual(saved["invalid"], "REQ-22")


if __name__ == "__main__":
    unittest.main()
