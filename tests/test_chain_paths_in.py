"""道の検めのパスの読み取り（chain_score.paths_in）：引数の文字列を語に分け、語ごとにドライブからのパス・/ で始まるパス・
.. を含む相対のパスを全部拾う。URL はパスにしない。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import chain_score  # noqa: E402

CWD = os.path.join(tempfile.gettempdir(), "local", "own")


def rel(p):
    return Path(os.path.normpath(os.path.join(CWD, p)))


class PathsIn(unittest.TestCase):
    def test_drive_path_does_not_hide_other_forms(self):
        """ドライブからのパスがあっても、/ で始まるパスと .. を含む相対のパスも拾う。"""
        self.assertEqual(chain_score.paths_in(r"type C:\src\a.txt && cat /x/b.txt", None), [r"C:\src\a.txt", "/x/b.txt"])
        hits = chain_score.paths_in(r"copy C:\src\a.txt ../other/c.txt", CWD)
        self.assertEqual(hits[0], r"C:\src\a.txt")
        self.assertEqual(Path(hits[1]), rel("../other/c.txt"))
        self.assertEqual(len(hits), 2)

    def test_every_slash_path_in_the_string(self):
        """/ で始まるパスは、文字列の頭でなくても全部拾う（cat /a/b /c/d の 2 つ）。"""
        self.assertEqual(chain_score.paths_in("cat /a/b /c/d", CWD), ["/a/b", "/c/d"])
        self.assertEqual(chain_score.paths_in("/a/b", CWD), ["/a/b"])

    def test_separators_quote_equals_paren(self):
        """引用符・=・( でも語を分ける。"""
        self.assertEqual(chain_score.paths_in("--path=/x/y", CWD), ["/x/y"])
        self.assertEqual(chain_score.paths_in("""open('/x/y') --out="/z" f(/w""", CWD), ["/x/y", "/z", "/w"])
        self.assertEqual(chain_score.paths_in(r'--dir=C:\a\b "D:/c"', CWD), [r"C:\a\b", "D:/c"])

    def test_urls_are_not_paths(self):
        """:// を含む語（URL）はパスにしない（.. を含んでも）。"""
        self.assertEqual(chain_score.paths_in("https://example.com/a/../b http://x/y ftp://h/p", CWD), [])
        self.assertEqual(chain_score.paths_in("curl https://example.com/x -o /tmp/y", CWD), ["/tmp/y"])

    def test_relative_without_dotdot_and_plain_words_are_ignored(self):
        self.assertEqual(chain_score.paths_in("src/a.py mujoco geom plane", CWD), [])
        self.assertEqual(chain_score.paths_in("../x", None), [])


class Audit(unittest.TestCase):
    """道の検めの全体（strays）で、文字列の頭でないパスが当たりになる。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = Path(tmp.name) / "local"
        self.own, self.other = self.local / "own", self.local / "other"
        for d in (self.own, self.other):
            d.mkdir(parents=True)

    def log(self, tool, params):
        events = [{"event": "init", "init": {"cwd": str(self.own)}},
                  {"event": "step_update", "step_update": {"step_index": 1, "state": "DONE", "step_type": "tool",
                                                           "tool_info": {"name": tool, "parameters": params}}}]
        p = self.own / "implementer-0-1.log"
        p.write_text("{}" + bench_run.LOG_SPLIT + "\n".join(map(json.dumps, events)) + "\n" + bench_run.LOG_SPLIT + "e\n", encoding="utf-8")
        return p

    def test_second_path_of_a_command_is_found(self):
        a, b = self.own / "a.py", self.other / "b.py"
        found = chain_score.strays([self.log("run_command", {"CommandLine": f"cat {a} {b}"})], self.own, self.local)
        self.assertEqual([Path(f[2]) for f in found], [b])

    def test_option_value_path_is_found(self):
        b = self.other / "b.py"
        found = chain_score.strays([self.log("run_command", {"CommandLine": f"tool --path={b}"})], self.own, self.local)
        self.assertEqual([Path(f[2]) for f in found], [b])


if __name__ == "__main__":
    unittest.main()
