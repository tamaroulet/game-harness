"""承認まわりの部品：承認ゲート（.github/scripts/ms4_approval_gate.py）と、main のルールセットの写し（.github/ruleset-main.json）。

    python -m unittest tests.test_approval -v
"""
import importlib.util
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

GATE_PATH = ROOT / ".github" / "scripts" / "ms4_approval_gate.py"
spec = importlib.util.spec_from_file_location("ms4_approval_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

class FakeAPI:
    def __init__(self, head_sha="a" * 40, labels=("ms4:approved",), events=None):
        self.head_sha = head_sha
        self.labels = set(labels)
        self.events = events if events is not None else [
            {"event": "labeled", "label": {"name": "ms4:approved"}, "actor": {"login": "tamaroulet"}}]
        self.calls = []

    def __call__(self, method, path, allow_404=False):
        self.calls.append((method, path))
        if method == "DELETE" and "/labels/" in path:
            self.labels.discard(path.rsplit("/", 1)[1])
            return None
        if path.endswith("/pulls/7"):
            return {"head": {"sha": self.head_sha}, "labels": [{"name": l} for l in self.labels]}
        if "/issues/7/events" in path:
            page = int(path.rsplit("page=", 1)[1])
            return self.events[(page - 1) * 100: page * 100]
        raise AssertionError(path)


def event(action, sha="a" * 40, labels=("ms4:approved",)):
    return {"action": action,
            "pull_request": {"number": 7, "head": {"sha": sha},
                             "labels": [{"name": l} for l in labels]}}


APPROVERS = {"tamaroulet"}


class ApprovalGateTests(unittest.TestCase):
    def test_labeled_by_approver_on_current_head_passes(self):
        ok, reason = gate.decide(event("labeled"), FakeAPI(), "o/r", APPROVERS)
        self.assertTrue(ok, reason)

    def test_synchronize_revokes_the_label_and_fails(self):
        api = FakeAPI()
        ok, reason = gate.decide(event("synchronize"), api, "o/r", APPROVERS)
        self.assertFalse(ok)
        self.assertIn(("DELETE", "/repos/o/r/issues/7/labels/ms4:approved"), api.calls)
        self.assertNotIn("ms4:approved", api.labels)

    def test_synchronize_without_label_fails_without_deleting(self):
        api = FakeAPI(labels=())
        ok, _ = gate.decide(event("synchronize", labels=()), api, "o/r", APPROVERS)
        self.assertFalse(ok)
        self.assertEqual([c for c in api.calls if c[0] == "DELETE"], [])

    def test_push_between_event_and_evaluation_fails(self):
        """ラベル付与と push がほぼ同時。イベントの SHA と今の head が違えば赤。"""
        ok, reason = gate.decide(event("labeled", sha="a" * 40), FakeAPI(head_sha="b" * 40),
                                 "o/r", APPROVERS)
        self.assertFalse(ok)
        self.assertIn("push", reason)

    def test_label_added_by_non_approver_fails(self):
        api = FakeAPI(events=[{"event": "labeled", "label": {"name": "ms4:approved"},
                               "actor": {"login": "someone-else"}}])
        ok, _ = gate.decide(event("labeled"), api, "o/r", APPROVERS)
        self.assertFalse(ok)

    def test_last_labeler_counts_not_the_first(self):
        """承認者が付けた後に外され、別人が付け直したら赤。"""
        api = FakeAPI(events=[
            {"event": "labeled", "label": {"name": "ms4:approved"}, "actor": {"login": "tamaroulet"}},
            {"event": "unlabeled", "label": {"name": "ms4:approved"}, "actor": {"login": "tamaroulet"}},
            {"event": "labeled", "label": {"name": "ms4:approved"}, "actor": {"login": "intruder"}},
        ])
        ok, _ = gate.decide(event("labeled"), api, "o/r", APPROVERS)
        self.assertFalse(ok)

    def test_events_are_read_across_pages(self):
        filler = [{"event": "commented"}] * 100
        api = FakeAPI(events=filler + [{"event": "labeled", "label": {"name": "ms4:approved"},
                                        "actor": {"login": "tamaroulet"}}])
        ok, reason = gate.decide(event("labeled"), api, "o/r", APPROVERS)
        self.assertTrue(ok, reason)

    def test_unlabeled_fails(self):
        ok, _ = gate.decide(event("unlabeled", labels=()), FakeAPI(labels=()), "o/r", APPROVERS)
        self.assertFalse(ok)

    def test_empty_approvers_fails_closed(self):
        api = FakeAPI()
        ok, reason = gate.decide(event("labeled"), api, "o/r", set())
        self.assertFalse(ok)
        # 後段の承認者照合でも赤にはなる。ここで見るのは、人間が原因を読めること
        #（一覧が空・読めない、と言う）と、API を読む前に止まること
        self.assertIn("空か読めません", reason)
        self.assertEqual(api.calls, [])

    def test_approvers_file_ignores_comments_and_case(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ms4-approvers"
            p.write_text("# 説明\n\nTamaRoulet\n", encoding="utf-8")
            self.assertEqual(gate.read_approvers(p), {"tamaroulet"})
            self.assertEqual(gate.read_approvers(Path(d) / "missing"), set())

    def test_workflow_uses_pull_request_target_and_never_checks_out_head(self):
        wf = (GATE_PATH.parent.parent / "workflows" / "approval.yml").read_text(encoding="utf-8")
        self.assertIn("pull_request_target:", wf)
        self.assertNotRegex(wf, r"(?m)^\s*pull_request:\s*$")
        self.assertIn("synchronize", wf)
        # 信頼する設定は既定ブランチの「今の最新」から読む。base.sha は PR 作成時点に
        # 固定されうるので checkout に使わない（承認者一覧の変更が既存 PR に効かなくなる）
        refs = re.findall(r"(?m)^\s*ref:\s*(.+?)\s*$", wf)
        self.assertEqual(refs, ["${{ github.event.repository.default_branch }}"])
        self.assertNotRegex(wf, r"ref:\s*\$\{\{\s*github\.event\.pull_request\.(base|head)\.")
        self.assertNotIn("head.sha", wf)
        self.assertNotRegex(wf, r"(?m)^\s*runs-on:.*self-hosted")
        self.assertRegex(wf, r"(?m)^\s*runs-on:\s*ubuntu-latest\s*$")
        self.assertRegex(wf, r"(?m)^\s*approval:\s*$", "必須チェック名はジョブ名 approval")


class RulesetTests(unittest.TestCase):
    """main に掛けたルールセットの写し。変えるときは、この写しを直してから GitHub の設定に当てる。"""

    def setUp(self):
        self.rs = json.loads((ROOT / ".github" / "ruleset-main.json").read_text(encoding="utf-8"))
        self.rules = {r["type"]: r for r in self.rs["rules"]}

    def test_nobody_can_bypass(self):
        self.assertEqual(self.rs["bypass_actors"], [], "所有者のトークンも迂回できないこと")
        self.assertEqual(self.rs["enforcement"], "active")
        self.assertEqual(self.rs["conditions"]["ref_name"]["include"], ["~DEFAULT_BRANCH"])

    def test_pr_required_and_history_protected(self):
        for t in ("pull_request", "non_fast_forward", "deletion", "required_status_checks"):
            self.assertIn(t, self.rules)
        self.assertEqual(self.rules["pull_request"]["parameters"]["required_approving_review_count"], 0,
                         "同じアカウントは自分の PR を approve できない。承認は approval チェックで表す")

    def test_both_required_checks_from_github_actions(self):
        checks = self.rules["required_status_checks"]["parameters"]["required_status_checks"]
        self.assertEqual({c["context"] for c in checks}, {"test", "approval"})
        self.assertTrue(all(c.get("integration_id") == 15368 for c in checks),
                        "github-actions 以外（手で作ったステータス等）で満たせないこと")


if __name__ == "__main__":
    unittest.main()
