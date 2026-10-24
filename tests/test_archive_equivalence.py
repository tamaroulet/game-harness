"""台帳分割（docs/progress.yaml + docs/progress_archive.yaml）の等価性テスト。

過去ログ分離前の単一 YAML（208 件）と、分離後の分割 YAML（42 件 + 166 件）を
progress.read_ref で読み込んだ際、まったく同じ完全な state が得られ、
かつ下流コンポーネント（goal_distance, hline_queue, hline_report 等）の出力が
完全に一致することをサブプロセスなしで高速かつ決定論的に検証する。
"""
import unittest
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import progress  # noqa: E402
import goal_distance  # noqa: E402


class ArchiveEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.active_path = ROOT / progress.REL_PATH
        cls.archive_path = ROOT / progress.ARCHIVE_REL_PATH
        cls.active_text = cls.active_path.read_text(encoding="utf-8")
        cls.archive_text = cls.archive_path.read_text(encoding="utf-8")

        # 分割後の各 YAML をパース
        active_state = yaml.safe_load(cls.active_text)
        archive_state = yaml.safe_load(cls.archive_text)

        # 分割前の単一巨大 YAML を忠実に再現（archive 宣言なし）
        monolithic_state = dict(active_state)
        monolithic_state.pop("archive", None)
        monolithic_state["tasks"] = list(archive_state["tasks"]) + list(active_state.get("tasks", []))
        cls.monolithic_text = yaml.safe_dump(monolithic_state, allow_unicode=True, sort_keys=False)

    def _show_fn_split(self, rel_path):
        if rel_path == progress.REL_PATH:
            return self.active_text
        if rel_path == progress.ARCHIVE_REL_PATH:
            return self.archive_text
        return None

    def _show_fn_monolithic(self, rel_path):
        if rel_path == progress.REL_PATH:
            return self.monolithic_text
        if rel_path == progress.ARCHIVE_REL_PATH:
            return None
        return None

    def test_read_ref_exact_match(self):
        st_mono = progress.read_ref(self._show_fn_monolithic)
        st_split = progress.read_ref(self._show_fn_split)
        self.assertIsNotNone(st_mono)
        # archive 宣言の有無を除き、タスク・ノード・制約等が完全一致すること
        st_split_comparable = dict(st_split)
        st_split_comparable.pop("archive", None)
        self.assertEqual(st_mono, st_split_comparable)
        self.assertEqual(len(st_split["tasks"]), len(st_mono["tasks"]))

    def test_progress_report_exact_match(self):
        st_mono = progress.read_ref(self._show_fn_monolithic)
        st_split = progress.read_ref(self._show_fn_split)
        rep_mono = progress.report(st_mono)
        rep_split = progress.report(st_split)
        self.assertEqual(rep_mono, rep_split)

    def test_hline_queue_read_ledger_structure(self):
        st_mono = progress.read_ref(self._show_fn_monolithic)
        st_split = progress.read_ref(self._show_fn_split)

        def to_queue_map(tasks):
            return {t["id"]: {"deps": list(t.get("depends_on") or []), "done": t.get("status") == "completed",
                              "status": t.get("status"), "by": t.get("by") or "hline", "oracle": list(t.get("oracle") or []), "row": t,
                              "after": list(t.get("after") or []), "rung": t.get("rung")} for t in tasks}

        map_mono = to_queue_map(st_mono["tasks"])
        map_split = to_queue_map(st_split["tasks"])
        self.assertEqual(map_mono, map_split)

    def test_goal_distance_prerequisites_exact_match(self):
        st_mono = progress.read_ref(self._show_fn_monolithic)
        st_split = progress.read_ref(self._show_fn_split)
        for rung in goal_distance.RUNGS:
            note_mono = goal_distance.prerequisite_note(st_mono["tasks"], rung)
            note_split = goal_distance.prerequisite_note(st_split["tasks"], rung)
            self.assertEqual(note_mono, note_split, f"rung {rung} の前提タスク注記が不一致です")

    def test_read_ref_without_archive(self):
        # 1. archive 宣言がある現役台帳で過去ログが読めなければ ProgressError
        def show_fn_no_archive(rel_path):
            if rel_path == progress.REL_PATH:
                return self.active_text
            return None
        with self.assertRaises(progress.ProgressError):
            progress.read_ref(show_fn_no_archive)

        # 2. archive 宣言の無い古い台帳なら progress.yaml 単独で正しく読めること
        def show_fn_legacy(rel_path):
            if rel_path == progress.REL_PATH:
                active_legacy = dict(yaml.safe_load(self.active_text))
                active_legacy.pop("archive", None)
                return yaml.safe_dump(active_legacy, allow_unicode=True)
            return None
        st = progress.read_ref(show_fn_legacy)
        self.assertIsNotNone(st)
        self.assertEqual(len(st["tasks"]), len(yaml.safe_load(self.active_text)["tasks"]))


if __name__ == "__main__":
    unittest.main()
