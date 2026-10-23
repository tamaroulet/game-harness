"""立て直しの間の安全の手当ての記録（W-147）。受入と試験が見る点は総監督が書いた（reply-architect-20261023-4 の 2）。

    python -m unittest tests.test_rebuild_hold

試験が見ること（総監督の一覧。削らない）:
1. docs/ops/rebuild_hold.yaml に 1〜8 の行がそろい、どの行も、扱い・確かめた命令・確かめた時刻・確かめた者・外す条件を持つ。
   扱いが見送りの行は理由を持つ
2. 4・5・6 の行の状態が Disabled
3. 1 の行の変えた日時が 2026-10-23 以降。記録のどこにも password の値の欄が無い
4. 「確かめた命令」に、書き換えの命令（Set-・New-・Enable-・Disable-・Register-・/grant・/deny・/remove）が入っていない

限界：CI が見るのは記録の形だけで、PC の実物は見ない。実物と記録が合うことは、読み取りの命令の出力を記録に写すことで担保する。
"""
import datetime
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "ops" / "rebuild_hold.yaml"
ROWS = range(1, 9)
FIELDS = ("target", "handling", "check_command", "checked_at", "checked_by", "release_condition")
HANDLINGS = ("済み", "見送り")
DISABLED = (4, 5, 6)
SINCE = datetime.datetime(2026, 10, 23, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
WRITES = ("set-", "new-", "enable-", "disable-", "register-", "/grant", "/deny", "/remove")


def when(value):
    """ISO 8601 の文字列（または YAML が読んだ日時）を、時差つきの日時にする。時差が無ければ日本時間とみなす。"""
    t = value if isinstance(value, datetime.datetime) else datetime.datetime.fromisoformat(str(value))
    return t if t.tzinfo else t.replace(tzinfo=SINCE.tzinfo)


def keys(node):
    """記録の中のすべての辞書の鍵。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from keys(v)


class RebuildHold(unittest.TestCase):
    def setUp(self):
        self.record = yaml.safe_load(RECORD.read_text(encoding="utf-8"))
        self.rows = {r["id"]: r for r in self.record["rows"]}

    def test_every_row_is_there_with_its_fields(self):
        self.assertEqual(sorted(self.rows), list(ROWS))
        for i, r in self.rows.items():
            with self.subTest(row=i):
                for key in FIELDS:
                    self.assertTrue(str(r.get(key) or "").strip(), key)
                self.assertIn(r["handling"], HANDLINGS)
                if r["handling"] == "見送り":
                    self.assertTrue(str(r.get("reason") or "").strip())
                when(r["checked_at"])

    def test_the_scheduled_starts_are_disabled(self):
        for i in DISABLED:
            self.assertEqual(self.rows[i].get("state"), "Disabled", i)

    def test_the_bench_password_was_changed_and_its_value_is_not_kept(self):
        self.assertGreaterEqual(when(self.rows[1]["changed_at"]), SINCE)
        self.assertFalse([k for k in keys(self.record) if "password" in k.lower()])

    def test_the_check_commands_only_read(self):
        for i, r in self.rows.items():
            with self.subTest(row=i):
                command = str(r["check_command"]).lower()
                self.assertFalse([w for w in WRITES if w in command], r["check_command"])


if __name__ == "__main__":
    unittest.main()
