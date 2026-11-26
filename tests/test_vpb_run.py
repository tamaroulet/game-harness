"""VibePrevisBench（text）の薄い接続（harness/vpb_run.py）のベンチ固有の部品の試験（docker に依らない）。数は作り物。

    python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import phase1_dev  # noqa: E402
import vpb_run  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_agent_runs_like_the_official_steps(self):
        cfg = vpb_run.load_config()
        self.assertEqual((cfg["sandbox"]["uid"], cfg["sandbox"]["home"], cfg["sandbox"]["workdir"]),
                         (0, "/root", "/work/blender_agent"))
        self.assertEqual((cfg["vpb"]["cpus"], cfg["vpb"]["budget_s"]), ("8", 14400))
        # メモリの上限は公式の既定（16 GB）ではなく、WSL の VM の物理メモリ（約 15 GiB）の下に置く（ホストを守る）
        self.assertEqual((cfg["vpb"]["memory"], cfg["vpb"]["memory_official"]), ("12g", "16g"))
        self.assertLess(int(cfg["vpb"]["memory"].rstrip("g")), 15)
        self.assertRegex(cfg["vpb"]["repo_commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(cfg["vpb"]["image_id"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("huggingface.co", cfg["network"]["allow"])

    def test_only_dev_tasks_run(self):
        cfg = vpb_run.load_config()
        self.assertEqual(vpb_run.require_dev(cfg, "TP152"), "TP152")
        for bad in ("TP001", "", "TP152;id"):
            with self.assertRaises(phase1_dev.NotDevError):
                vpb_run.require_dev(cfg, bad)


class LayerTests(unittest.TestCase):
    ROW = {"dims": {
        "A1": {"score": 0.5, "slots": [{"slot": "entities", "w": 0.5, "score": 1.0, "members": [{"src": "509", "n": 4}]},
                                       {"slot": "counts", "w": 0.5, "score": 0.0, "members": [{"src": "509", "n": 2}]}]},
        "A2": {"score": 0.25, "slots": [{"slot": "v5_layout", "w": 1.0, "score": 0.25, "members": [{"src": "v5", "n": 8}]}]},
        "A3": None,
        "D1": {"score": 0.4, "gate": {"raw": 0.8, "g": 0.5},
               "slots": [{"slot": "penetration", "w": 0.5, "score": 1.0, "members": [{"src": "509", "n": 1}]},
                         {"slot": "teleport", "w": 0.5, "score": 0.6, "members": [{"src": "509", "n": 1}]}]}}}

    def test_rule_is_the_mean_of_the_dimensions_with_a_score(self):
        got = vpb_run.layers(self.ROW)
        self.assertAlmostEqual(got["rule"], 38.333, places=3)
        self.assertIsNone(got["dims"]["A3"])

    def test_lost_points_split_into_layers_and_sum_to_the_gap(self):
        lost = vpb_run.layers(self.ROW)["lost"]
        self.assertAlmostEqual(lost["gt_facts"], 16.667, places=3)    # A1 の counts（隠しの GT から）
        self.assertAlmostEqual(lost["claims"], 25.0, places=3)        # A2 の v5_layout（課題文の引用つき）
        self.assertAlmostEqual(lost["D1"], 6.667, places=3)           # 物理の素点の落ち（teleport）
        self.assertAlmostEqual(lost["D1_gate"], 13.333, places=3)     # 内容の関門の減点（raw × (1 − g)）
        self.assertAlmostEqual(sum(lost.values()), 100 - 38.333, places=2)


class PartsTests(unittest.TestCase):
    def test_sums_and_claims(self):
        self.assertEqual(vpb_run.sums("abc  tasks/TP152/task.json\n\ndef  categories.json\n"),
                         {"tasks/TP152/task.json": "abc", "categories.json": "def"})
        v5 = {"claims": [{"n": 2, "src": 2, "block": "layout", "text": "count x = 2", "status": "false"},
                         {"n": 3, "src": 3, "block": "entity", "text": "P01 on y", "status": "missing"}],
              "camera_checks": [{"n": 1, "t": "cam_lens", "text": "lens wide", "status": "true"}]}
        claims = {"claims": [{"n": 1, "cite": "first"}, {"n": 2, "cite": "second"}, {"n": 3, "cite": "third"}]}
        got = vpb_run.claim_results(v5, claims)
        self.assertEqual([(c["block"], c["status"], c["cite"]) for c in got],
                         [("layout", "false", "second"), ("entity", "missing", "third"), ("camera", "true", None)])


class ReplayPolicyTests(unittest.TestCase):
    """replay の落ちの扱い（公式の classify.py の分けと aggregate.py の方針：インフラの落ちは未採点・再走 2 回まで）。"""
    ENV = {"status": "failed", "stop_reason": "single_submission", "failure_category": "environment_failure",
           "error": "Delivery checks failed: editable_scene_open"}

    def test_outcome_follows_the_official_classes(self):
        self.assertEqual(vpb_run.replay_outcome({"ok": True, "attempts": [self.ENV, {"status": "ok"}]}), ("delivered", "ok"))
        self.assertEqual(vpb_run.replay_outcome({"ok": False, "attempts": [self.ENV] * 3}), ("infra", "environment_failure"))
        self.assertEqual(vpb_run.replay_outcome({"ok": False, "attempts": [{"failure_category": "api_protocol_error"}]})[0],
                         "infra")
        self.assertEqual(vpb_run.replay_outcome({"ok": False, "attempts": [{"stop_reason": "launch_error"}]})[0], "infra")
        self.assertEqual(vpb_run.replay_outcome(None), ("infra", "no_replay_json"))
        self.assertEqual(vpb_run.replay_outcome({"ok": False, "attempts": [{"failure_category": "model_code_error"}]}),
                         ("model", "model_code_error"))

    def test_infra_failures_rerun_at_most_twice(self):
        cfg = vpb_run.load_config()
        n = cfg["vpb"]["max_rerun_batches"]
        self.assertEqual(n, 2)
        self.assertEqual(vpb_run.next_action(["infra"], n), "rerun")
        self.assertEqual(vpb_run.next_action(["infra", "infra"], n), "rerun")
        self.assertEqual(vpb_run.next_action(["infra", "infra", "infra"], n), "infra_exhausted")
        self.assertEqual(vpb_run.next_action(["infra", "delivered"], n), "score")
        self.assertEqual(vpb_run.next_action(["model"], n), "zero")

    def test_environment_failure_is_never_zero(self):
        got = vpb_run.final_aggregate(True, "infra_exhausted", "environment_failure", {"skipped": "x"})
        self.assertIsNone(got["rule"])
        self.assertEqual(got["status"], "unscored")
        dump_failed = vpb_run.final_aggregate(True, "score", "ok", {"dump": {"ok": False}})
        self.assertEqual((dump_failed["rule"], dump_failed["status"]), (None, "unscored"))
        self.assertEqual(vpb_run.final_aggregate(True, "zero", "no_solution", None)["rule"], 0.0)
        scored = vpb_run.final_aggregate(True, "score", "ok", {"scores_csv": {"status": "scored", "rule": "84.084"},
                                                               "layers": {"lost": {"D1": 1.0}}})
        self.assertEqual((scored["rule"], scored["status"], scored["lost"]), (84.084, "scored", {"D1": 1.0}))
        self.assertEqual(vpb_run.final_aggregate(False, "score", "ok", None), {"rule": None, "status": "invalid_run"})

    def test_collect_failure_is_infra_not_zero(self):
        self.assertEqual(vpb_run.collect_outcome(1, False, None)[0], "collect_failed")          # collect が落ちた
        self.assertEqual(vpb_run.collect_outcome(124, False, True)[0], "collect_failed")        # 時間切れ
        self.assertEqual(vpb_run.collect_outcome(0, False, True)[0], "collect_failed")          # 写っていない
        self.assertEqual(vpb_run.collect_outcome(0, False, False), ("zero", "no_solution"))     # 提出物がどこにも無い
        self.assertEqual(vpb_run.collect_outcome(0, True, True), ("replay", "ok"))
        got = vpb_run.final_aggregate(True, "collect_failed", "collect rc=1", {"skipped": "x"})
        self.assertEqual((got["rule"], got["status"]), (None, "unscored"))

    def test_worker_lost_marks_and_kept_batches(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp) / "TP000"
            (t / "replay.infra0" / "delivery").mkdir(parents=True)
            (t / "replay.infra0" / "delivery" / "validation.json").write_text(json.dumps(
                {"checks": [{"name": "editable_scene_open", "error": "workspace manifest failed: \n" + vpb_run.WORKER_LOST + "."}]}),
                encoding="utf-8")
            (t / "replay").mkdir()
            result = {"ok": False, "attempts": [self.ENV, dict(self.ENV, stop_reason="delivery_failed",
                                                                 error="x: " + vpb_run.WORKER_LOST)]}
            (t / "replay.json").write_text(json.dumps(result), encoding="utf-8")
            (t / "solution.py").write_text("pass\n", encoding="utf-8")
            self.assertEqual(vpb_run.worker_lost_attempts(t, result), [True, True])
            self.assertEqual(vpb_run.worker_lost_attempts(t, {"ok": False, "attempts": [{"error": "other"}]}), [False])
            moved = vpb_run.keep_batch(t, Path(tmp) / "out" / "replay_batch0")
            self.assertEqual(moved, ["replay", "replay.infra0", "replay.json"])
            self.assertEqual(sorted(p.name for p in t.iterdir()), ["solution.py"])


if __name__ == "__main__":
    unittest.main()
