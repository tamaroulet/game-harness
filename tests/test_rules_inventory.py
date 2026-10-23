"""決まりの棚卸しの表（docs/design/rules_inventory.md、W-169）。受入と試験が見る点は総監督が書いた
（reply-architect-20261023-2 の (c)）。

    python -m unittest tests.test_rules_inventory

試験が見ること（総監督の一覧。削らない）:
1. 表のどの行も分類・根拠・扱いを持つ
2. 「機械で守られている」の path:行 が実在する
3. 足した文の決まりが 0 件
4. setup が書く受信箱の CLAUDE.md に、表で「消す」とした文が無い

3 は、扱いの語に「足す」が無いことと、受信箱の CLAUDE.md と game-harness の CLAUDE.md の箇条がどれも、表の消す以外の行の
引用で覆われていること（表に無い決まりが文書に無い）で見る。4 は、残す側の引用が setup の書いた文にあることも見る。
会話の席の記憶と CLAUDE.md の行は、操縦士が決めた印を持つ（受入 3）。
"""
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import hline_room  # noqa: E402

TABLE = ROOT / "docs" / "design" / "rules_inventory.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
KINDS = ("機械", "効かない", "ぶつかる", "形")
HOWS = ("残す", "消す", "機械に移す", "役の分け方に移す")
SOURCES = {"RM": "受信箱の CLAUDE.md", "CL": "CLAUDE.md", "RS": "調べ方の規則", "MM": "会話の席の記憶",
           "IX": "総監督の文書", "PR": "総監督の文書", "NM": "総監督の文書", "IV": "総監督の文書", "PL": "総監督の文書"}
MARK = re.compile(r"^操縦士 \d{4}-\d{2}-\d{2}$")
PATH_LINE = re.compile(r"`([\w./-]+\.\w+):(\d+)(?:-(\d+))?`")
ROW = re.compile(r"^\| ([A-Z]{2})-\d\d \|")
BULLET = re.compile(r"^( {0,3})(?:[-*]|\d+\.)\s+(.*)$")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def read(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


def rows():
    return [cells(l) for l in read(TABLE).splitlines() if ROW.match(l)]


def quotes(cell):
    return re.findall(r"「(.+?)」", cell)


def bullets(text):
    """箇条（- か 1. で始まる、字下げ 3 以下の行）ごとに、続く字下げの行をつないだ文。"""
    out = []
    for line in text.splitlines():
        m = BULLET.match(line)
        if m:
            out.append(m.group(2).strip())
        elif out and line.startswith(" ") and line.strip():
            out[-1] += line.strip()
        elif line.strip():
            out.append(None)   # 字下げの無い地の文・見出しで箇条が終わる
    return [b for b in out if b]


def found(quote, *texts):
    return all(any(part in t for t in texts) for part in quote.split("…") if part)


def setup_text():
    """python -m harness.hline setup が受信箱に書く CLAUDE.md（設定と report.md は差し替える）。"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cfg = {"inbox": str(root / "inbox"), "out": str(root / "out")}
        with mock.patch.object(hline_room, "write_report"), \
                mock.patch.object(hline_room, "load_state", return_value={}), \
                mock.patch.object(hline_room, "director_settings", return_value={}), \
                mock.patch("builtins.print"):
            hline_room.setup(cfg)
        return read(root / "inbox" / "CLAUDE.md")


class RulesInventory(unittest.TestCase):
    def setUp(self):
        self.rows = rows()
        self.text = read(TABLE)

    def of(self, prefix):
        return [r for r in self.rows if r[0].startswith(prefix + "-")]

    # 1
    def test_every_row_has_a_kind_a_reason_and_a_handling(self):
        self.assertGreater(len(self.rows), 100)
        self.assertEqual(len({r[0] for r in self.rows}), len(self.rows), "ID が重なっている")
        for r in self.rows:
            with self.subTest(row=r[0]):
                self.assertEqual(len(r), 7)
                self.assertTrue(all(r))
                self.assertIn(r[3], KINDS)
                self.assertTrue(r[5].startswith(HOWS), r[5])
                if r[3] != "形":
                    self.assertFalse(r[4].startswith("—"), "機械・効かない・ぶつかるの行は根拠を書く")
        for prefix in ("RM", "CL", "RS", "MM", "IX", "PR", "NM", "IV", "PL"):
            self.assertTrue(self.of(prefix), prefix)

    # 2
    def test_the_path_lines_exist(self):
        for r in self.rows:
            refs = PATH_LINE.findall(r[4])
            if r[3] == "機械":
                with self.subTest(row=r[0]):
                    self.assertTrue(refs, "機械で守られている行は、守る場所の path:行 を書く")
            for path, first, last in refs:
                with self.subTest(row=r[0], ref=f"{path}:{first}"):
                    p = ROOT / path
                    self.assertTrue(p.is_file(), path)
                    n = len(read(p).splitlines())
                    self.assertLessEqual(int(last or first), n)
                    self.assertLessEqual(int(first), int(last or first))

    # 3
    def test_no_rule_was_added(self):
        self.assertFalse([r[0] for r in self.rows if "足す" in r[5].split("（")[0]])
        for prefix, path, text in (("RM", hline_room.TEMPLATE, read(hline_room.TEMPLATE)), ("CL", CLAUDE_MD, read(CLAUDE_MD))):
            kept = [q for r in self.of(prefix) if not r[5].startswith("消す") for q in quotes(r[2])]
            for b in bullets(text):
                with self.subTest(file=prefix, bullet=b[:30]):
                    self.assertTrue(any(found(q, b) for q in kept), "表に無い決まりが文書にある")
        self.assertIn("- 新しく足した文の決まり: 0", self.text)

    def test_the_summary_is_counted_from_the_table(self):
        src = Counter(SOURCES[r[0][:2]] for r in self.rows)
        kinds = Counter(r[3] for r in self.rows)
        hows = Counter(next(h for h in HOWS if r[5].startswith(h)) for r in self.rows)
        before = [r for r in self.rows if r[3] != "機械"]
        after = [r for r in before if not r[5].startswith("消す")]
        order = ("受信箱の CLAUDE.md", "CLAUDE.md", "調べ方の規則", "会話の席の記憶", "総監督の文書")
        self.assertIn(f"- 行: 計 {len(self.rows)}（" + "・".join(f"{k} {src[k]}" for k in order) + "）", self.text)
        self.assertIn("- 分類: " + "・".join(f"{k} {kinds[k]}" for k in KINDS), self.text)
        self.assertIn("- 扱い: " + "・".join(f"{k} {hows[k]}" for k in HOWS), self.text)
        self.assertIn(f"- 文の決まり（分類が機械でない行）: 前 {len(before)} → 後 {len(after)}", self.text)
        self.assertLess(len(after), len(before))

    # 4
    def test_setup_writes_the_room_as_the_table_says(self):
        text = setup_text()
        self.assertEqual(text, read(hline_room.TEMPLATE))
        for r in self.of("RM"):
            for q in quotes(r[2]):
                with self.subTest(row=r[0], quote=q[:30]):
                    if r[5].startswith("消す"):
                        self.assertFalse(found(q, text), "表で消すとした文が残っている")
                    else:
                        self.assertTrue(found(q, text, *bullets(text)), "表で残すとした文が無い")

    def test_the_pilot_marked_the_memory_and_claude_md_rows(self):
        for r in self.of("MM") + self.of("CL"):
            with self.subTest(row=r[0]):
                self.assertRegex(r[6], MARK)
        for r in self.of("CL"):
            if r[5].startswith("消す"):
                for q in quotes(r[2]):
                    self.assertNotIn(q, read(CLAUDE_MD))


if __name__ == "__main__":
    unittest.main()
