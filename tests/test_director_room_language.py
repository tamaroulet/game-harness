"""総監督の部屋の CLAUDE.md に、書く言語を日本語と定める節があること（元のテンプレートと、setup が受信箱に写したもの）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import hline_room  # noqa: E402

SECTION = (
    "## 書く言語\n"
    "総監督の応答・報告・分析・What の起草は、すべて日本語で書く。英語の文を書かない（コード・パス・コマンド・識別子の字面は元のまま）。\n"
    "応答を送る前に、見出し・表・箇条書きを含めて、英語の文が混ざっていないかを確かめる。\n"
)
NEXT = "## 起動時に読む順序"


def read(path):
    return Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")


class DirectorRoomLanguageTest(unittest.TestCase):
    def assert_section_before_reading_order(self, text):
        self.assertEqual(text.count(SECTION), 1)
        self.assertIn(NEXT, text)
        self.assertLess(text.index(SECTION), text.index(NEXT))
        self.assertIn(SECTION + "\n" + NEXT, text)

    def test_template_has_the_language_section(self):
        self.assert_section_before_reading_order(read(hline_room.TEMPLATE))

    def test_setup_copies_the_section_into_the_inbox(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = {"inbox": str(root / "inbox"), "out": str(root / "out")}
            with mock.patch.object(hline_room, "write_report"), \
                    mock.patch.object(hline_room, "load_state", return_value={}), \
                    mock.patch.object(hline_room, "director_settings", return_value={}), \
                    mock.patch("builtins.print"):
                hline_room.setup(cfg)
            self.assert_section_before_reading_order(read(root / "inbox" / "CLAUDE.md"))


if __name__ == "__main__":
    unittest.main()
