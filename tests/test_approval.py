"""承認まわりの部品。受入テストの抽出器（test_summary）と承認ゲート（ms4_approval_gate）。

    python -m unittest discover -s tests -v
"""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import test_summary  # noqa: E402

GATE_PATH = ROOT / "harness" / "templates" / "game-repo" / ".github" / "scripts" / "ms4_approval_gate.py"
spec = importlib.util.spec_from_file_location("ms4_approval_gate", GATE_PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

FIXTURE = ROOT / "tests" / "fixtures" / "BossChargeStateTests.cs"


# ============================================================ 抽出器

class TestSummaryTests(unittest.TestCase):
    def test_real_acceptance_tests_are_all_listed(self):
        """Issue #2 で分解役が書いた実物。[Test] の数と Assert の数が一致すること。"""
        src = FIXTURE.read_text(encoding="utf-8-sig")
        tests = test_summary.summarize_source(src)
        self.assertEqual(len(tests), src.count("[Test]"))
        self.assertEqual(len(tests), 22)
        self.assertEqual(sum(len(t["asserts"]) for t in tests), src.count("Assert."))
        first = tests[0]
        self.assertEqual(first["name"], "Idle_BeforeBeginCharge_NeverDashes")
        self.assertEqual(first["asserts"][0]["args"][0], "BossChargePhase.Idle")
        self.assertEqual(first["asserts"][0]["message"], "BeginCharge を呼ぶ前に突進してはならない")

    def test_synthetic_shapes(self):
        src = '''
        using NUnit.Framework;
        public class T {
            // [Test] コメントの中の属性と Assert.IsTrue(false); は数えない
            /* [Test] public void Commented() { Assert.Fail(); } */

            [Test]
            public void MultiLine_Assert()
            {
                Assert.AreEqual(
                    3,
                    Compute(1, 2),
                    "1 と 2 で 3; 括弧 ) も文字列の中なら無視");
            }

            [TestCase(0, 0)]
            [TestCase(-1, 0)]
            public void Clamp(int input, int expected)
            {
                Assert.That(Clamp(input), Is.EqualTo(expected));
            }

            [Test, Category("slow")]
            public void Throws() { Assert.Throws<System.ArgumentException>(() => Make(null)); }

            [Test]
            public void NoAssertion() { var x = 1; }

            public void Helper() { Assert.Fail("テストではない"); }

            [Test]
            public void Verbatim() { Assert.AreEqual(@"a""b", S(), @"メッセージ"); }
        }
        '''
        tests = {t["name"]: t for t in test_summary.summarize_source(src)}
        self.assertEqual(sorted(tests), ["Clamp", "MultiLine_Assert", "NoAssertion", "Throws", "Verbatim"])

        ml = tests["MultiLine_Assert"]["asserts"]
        self.assertEqual(len(ml), 1)
        self.assertEqual(ml[0]["args"], ["3", "Compute(1, 2)"])
        self.assertIn("括弧 ) も文字列", ml[0]["message"])

        self.assertEqual(tests["Clamp"]["cases"], ["0, 0", "-1, 0"])
        self.assertEqual(tests["Clamp"]["asserts"][0]["kind"], "That")
        self.assertEqual(tests["Throws"]["asserts"][0]["kind"], "Throws<System.ArgumentException>")
        self.assertEqual(tests["NoAssertion"]["asserts"], [])
        self.assertEqual(tests["Verbatim"]["asserts"][0]["args"], ['@"a""b"', "S()"])

        md = test_summary.to_markdown("T.cs", list(tests.values()))
        self.assertIn("**アサーション無し**", md)
        self.assertIn("TestCase(-1, 0)", md)


# ============================================================ 承認ゲート

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


class InstalledGateMatchesTemplateTests(unittest.TestCase):
    """harness 自身に入れた承認ゲートが、配布元の雛形と同じであること。

    写しが雛形とずれると、「検証済みの門」と「実際に動いている門」が別物になる。
    直すときは雛形を直し、写しを置き直す（写しを直接直さない）。
    """

    def test_installed_copy_is_byte_identical(self):
        template = ROOT / "harness" / "templates" / "game-repo" / ".github"
        installed = ROOT / ".github"
        for rel in ("workflows/approval.yml", "scripts/ms4_approval_gate.py", "ms4-approvers"):
            with self.subTest(rel):
                self.assertEqual((installed / rel).read_bytes(), (template / rel).read_bytes())


class RulesetTemplateTests(unittest.TestCase):
    """A4 で適用するルールセットの雛形。適用前に中身を機械で確かめる。"""

    def setUp(self):
        import json
        self.rs = json.loads((ROOT / "harness" / "templates" / "ruleset-main.json").read_text(encoding="utf-8"))
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
