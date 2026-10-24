"""ノードの説明の置き場（W-149。受入は総監督：受信箱の drafts/director/reply-architect-20261023-6.md）。

    python -m unittest tests.test_tree_docs

見る点（総監督の「試験が見ること」を削らない）:
1. 本物の台帳の完了でないノードとファイルが 1 対 1（完了でないノードはどれもファイルを持ち、台帳に無いノードのファイルは無い）
2. どのファイルも 7 つの見出しを持ち、承認の行が 3 つの欄を持つ（無ければ「なし」）
3. 台帳に note の欄が無い
4. 偽の台帳とファイルで、承認の無いノード・説明の古いノードに、ツリーが印を付ける
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import hline_report  # noqa: E402
import progress  # noqa: E402
import tree_docs  # noqa: E402
from tests import test_report_stopped as stopped  # noqa: E402


def ledger():
    return progress.load(ROOT / progress.REL_PATH)


def task(tid, group, status, **extra):
    return {"id": tid, "title": f"title {tid}", "group": group, "target_repo": "r", "status": status, **extra}


def fake():
    return {
        "project_goal": "g", "active_task_id": "T2", "constraints": ["c"],
        "nodes": [{"id": "A", "title": "root", "parent": None},
                  {"id": "OK", "title": "approved", "parent": "A"},
                  {"id": "NO", "title": "unapproved", "parent": "A"},
                  {"id": "OLD", "title": "stale", "parent": "A"},
                  {"id": "BARE", "title": "no doc", "parent": "A"},
                  {"id": "DONE", "title": "done", "parent": "A"}],
        "tasks": [task("T1", "OK", "completed", completed_at="2026-10-20T10:00:00"),
                  task("T2", "OK", "in_progress"),
                  task("T3", "NO", "pending"),
                  task("T4", "OLD", "completed", completed_at="2026-10-25T09:00:00"),
                  task("T5", "OLD", "pending"),
                  task("T6", "BARE", "pending"),
                  task("T7", "DONE", "completed")],
    }


APPROVAL = "| 日付 | 決めた者 | 決めたこと |\n|:--|:--|:--|\n| 2026-10-21 | 操縦士 | 目的の文を受けた |"


def doc(node_id, day="2026-10-24", approval=APPROVAL):
    text = tree_docs.skeleton(node_id, "t", day)
    return text.replace("## 承認\n\nなし", f"## 承認\n\n{approval}")


class RealLedgerTests(unittest.TestCase):
    def setUp(self):
        self.state = ledger()
        self.docs = tree_docs.read_dir(ROOT)

    def test_open_nodes_and_files_match(self):
        nodes = {n["id"] for n in self.state["nodes"]}
        open_nodes = {n["id"] for n in self.state["nodes"] if not progress._done(self.state, n)}
        self.assertEqual(open_nodes - set(self.docs), set(), "完了でないノードに説明のファイルが無い")
        self.assertEqual(set(self.docs) - nodes, set(), "台帳に無いノードの説明のファイルがある")

    def test_every_file_has_the_headings_and_approval_columns(self):
        self.assertTrue(self.docs)
        for node_id, text in self.docs.items():
            with self.subTest(node=node_id):
                self.assertEqual(tree_docs.problems(node_id, text), [])
                self.assertEqual([h for h in tree_docs.HEADINGS if h not in tree_docs.sections(text)], [])
                body = tree_docs.sections(text)["承認"]
                if body != tree_docs.NONE:
                    self.assertTrue(tree_docs.rows(body), "承認の表に行が無い（無ければ「なし」）")
                    for r in tree_docs.rows(body):
                        self.assertEqual(len(r), 3)
                        self.assertTrue(all(r))

    def test_ledger_has_no_note(self):
        self.assertEqual([n["id"] for n in self.state["nodes"] if "note" in n], [])
        self.assertEqual([t["id"] for t in self.state["tasks"] if "note" in t], [])
        self.assertEqual(progress.validate(self.state), [])

    def test_note_moved_to_w18(self):
        self.assertIn("基準は取り直してから使う", tree_docs.sections(self.docs["W-18"])["中身"])


class ShapeTests(unittest.TestCase):
    def test_missing_heading_is_a_problem(self):
        text = doc("X").replace("## 見送り", "## ほか")
        self.assertTrue(any("見送り" in p for p in tree_docs.problems("X", text)))

    def test_approval_row_needs_three_filled_columns(self):
        bad = "| 日付 | 決めた者 | 決めたこと |\n|:--|:--|:--|\n| 2026-10-21 | 操縦士 |  |"
        self.assertTrue(tree_docs.problems("X", doc("X", approval=bad)))
        self.assertTrue(tree_docs.problems("X", doc("X", approval="承認した")))
        self.assertEqual(tree_docs.problems("X", doc("X")), [])
        self.assertEqual(tree_docs.problems("X", doc("X", approval="なし")), [])

    def test_table_without_rows_or_with_loose_lines_is_a_problem(self):
        head = "| 日付 | 決めた者 | 決めたこと |\n|:--|:--|:--|"
        for body in (head, "", head + "\n2026-10-21 操縦士 受けた"):
            with self.subTest(body=body):
                self.assertTrue(tree_docs.problems("X", doc("X", approval=body)))

    def test_extra_or_repeated_heading_is_a_problem(self):
        self.assertTrue(tree_docs.problems("X", doc("X") + "\n## ほか\n\nx\n"))
        self.assertTrue(tree_docs.problems("X", doc("X") + "\n## 承認\n\nなし\n"))

    def test_validate_refuses_note(self):
        s = fake()
        s["nodes"][1]["note"] = "x"
        self.assertTrue(any("note" in p for p in progress.validate(s)))


class TreeMarkTests(unittest.TestCase):
    def setUp(self):
        self.docs = {"A": doc("A"), "OK": doc("OK"), "NO": doc("NO", approval="なし"),
                     "OLD": doc("OLD", day="2026-10-24")}

    def line(self, node_id, docs=None):
        text = progress.tree(fake(), True, self.docs if docs is None else docs)
        return next(l for l in text.splitlines() if f" {node_id} " in l)

    def test_marks_approval(self):
        self.assertIn("承認あり", self.line("OK"))
        self.assertNotIn("承認なし", self.line("OK"))
        self.assertIn("承認なし", self.line("NO"))

    def test_marks_stale_description(self):
        self.assertIn("説明が古い", self.line("OLD"))
        self.assertNotIn("説明が古い", self.line("OK"))
        self.assertNotIn("説明が古い", self.line("NO"))

    def test_marks_missing_description_and_skips_done_nodes(self):
        self.assertIn("説明なし", self.line("BARE"))
        self.assertNotIn("〔", self.line("DONE"))

    def test_report_carries_the_marks(self):
        text = progress.report(fake(), self.docs)
        self.assertIn("承認なし", text)
        self.assertIn("説明が古い", text)

    def test_done_root_has_no_mark(self):
        s = fake()
        for t in s["tasks"]:
            t["status"] = "completed"
        s["active_task_id"] = None
        self.assertNotIn("〔", progress.tree(s, True, self.docs))

    def test_without_docs_tree_is_unchanged(self):
        self.assertNotIn("〔", progress.tree(fake(), True))

    def test_stale_looks_only_at_tasks_directly_under_the_node(self):
        """子のノードの下のタスクが済んでも、親は古くならない（総監督の検め 9 本目 §3 の推奨）。"""
        s = fake()
        s["nodes"].append({"id": "KID", "title": "child", "parent": "OK"})
        s["tasks"].append(task("T8", "KID", "completed", completed_at="2026-10-30T09:00:00"))
        s["tasks"].append(task("T9", "KID", "pending"))
        docs = {**self.docs, "KID": doc("KID", day="2026-10-24")}
        text = progress.tree(s, True, docs)
        line = lambda nid: next(l for l in text.splitlines() if f" {nid} " in l)  # noqa: E731
        self.assertIn("説明が古い", line("KID"))
        self.assertNotIn("説明が古い", line("OK"))
        self.assertNotIn("説明が古い", line("A"))

    def test_complete_time_is_what_stale_compares(self):
        self.assertTrue(tree_docs.stale(doc("X", day="2026-10-24"), ["2026-10-25T00:00:01"]))
        self.assertFalse(tree_docs.stale(doc("X", day="2026-10-25"), ["2026-10-25T23:00:00"]))
        self.assertFalse(tree_docs.stale(doc("X"), [None]))


DOCS = {"A": doc("A"), "rebuild": doc("rebuild", approval="なし")}


def serve_docs(args):
    """git の ls-tree と show で、ノードの説明を返す（読めないものは None）。"""
    if args[0] == "ls-tree":
        return "".join(f"{tree_docs.DIR}/{k}.md\n" for k in DOCS)
    if args[0] == "show" and f":{tree_docs.DIR}/" in args[1]:
        return DOCS.get(Path(args[1].split(":", 1)[1]).stem)
    return None


class ReportMdTests(stopped.Base):
    """report.md の 2 つの書き手（走っている間・止まっている間）が、ref の版の説明から印を付ける。"""

    def test_stopped_report_marks_nodes(self):
        def run(args, cwd, ttl, label, env=None, input=None):
            if args[0] == "git" and args[1] in ("ls-tree", "show") and (out := serve_docs(args[1:])) is not None:
                return 0, out, ""
            return stopped.fake_run(args, cwd, ttl, label, env, input)
        self.put_state()
        hline_stopped = stopped.hline_stopped
        hline_stopped.stop(self.cfg, "立て直しの間は止める")
        text = hline_stopped.write(self.cfg, runner=run, clock=stopped.CLOCK)
        self.assertIn("- A root〔承認あり〕", text)
        self.assertIn("- [ ] rebuild 立て直し（0/1、止めた 1）〔承認なし〕", text)

    def test_running_report_marks_nodes(self):
        ledger = yaml_text(stopped.LEDGER)

        def git(args, ttl):
            return ledger if args == ["show", f"{self.cfg['base']}:{progress.REL_PATH}"] else serve_docs(args)
        with mock.patch.object(hline_report, "git_text", side_effect=git),                 mock.patch.object(hline_report, "remote_ref", return_value="origin/none"):
            text = hline_report.progress_report(self.cfg)
        self.assertIn("- A root〔承認あり〕", text)
        self.assertIn("〔承認なし〕", text)

    def test_unreadable_docs_leave_the_tree_unmarked(self):
        self.assertIsNone(tree_docs.read_git(lambda a: None, "origin/main"))
        self.assertNotIn("〔", progress.report(stopped.LEDGER, None))


def yaml_text(state):
    return progress.dump(state)


if __name__ == "__main__":
    unittest.main()
