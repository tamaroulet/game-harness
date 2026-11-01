"""検査の道具の意地悪な試験：道の検め（chain_score の strays・audit）と漏れの走査（bench_guard の leaks）のすり抜けを狙う。
Windows の形のパスは文字の形のまま渡す（道の検めはパスを文字で比べるので、どの OS でも同じに通る）。
expectedFailure は、すり抜けたまま残したもの（直しが大きいか、決めかねるもの）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_guard  # noqa: E402
import bench_run  # noqa: E402
import chain_score  # noqa: E402

MARKS = ("B09", "springchain", "docs/bench", "chain/s")
WIN_LOCAL = "C:\\src\\local"
WIN_OWN = WIN_LOCAL + "\\own"


class Logs(unittest.TestCase):
    """実装役の生ログを一時ディレクトリに書き、own・local（文字か Path）で道の検めを掛ける。"""
    own, local = WIN_OWN, WIN_LOCAL

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.n = 0

    def log(self, *steps):
        events = [{"event": "init", "init": {"cwd": str(self.own)}}]
        events += [{"event": "step_update", "step_update": {"step_index": i, "state": "DONE", "step_type": "tool",
                                                             "tool_info": {"name": t, "parameters": p}}} for i, (t, p) in enumerate(steps, 1)]
        self.n += 1
        p = self.tmp / f"implementer-0-{self.n}.log"
        p.write_text("{}" + bench_run.LOG_SPLIT + "\n".join(map(json.dumps, events)) + "\n" + bench_run.LOG_SPLIT + "e\n", encoding="utf-8")
        return p

    def audit(self, *steps):
        return chain_score.audit([self.log(*steps)], self.own, self.local, MARKS)

    def hits(self, *steps):
        return self.audit(*steps)[0]


class WindowsForms(Logs):
    def test_mixed_separators_and_case(self):
        """区切りと大文字小文字を混ぜても、外は当たり・中は当たらない。"""
        found = self.hits(("view_file", {"AbsolutePath": "C:/SRC/LOCAL/OTHER/x.py"}),
                          ("view_file", {"AbsolutePath": "c:\\src\\local\\other\\y.py"}),
                          ("view_file", {"AbsolutePath": "c:/Src\\Local/Other\\z.py"}),
                          ("view_file", {"AbsolutePath": "C:/SRC/LOCAL/OWN/w.py"}))
        self.assertEqual([f[2] for f in found], ["C:/SRC/LOCAL/OTHER/x.py", "c:\\src\\local\\other\\y.py", "c:/Src\\Local/Other\\z.py"])

    def test_long_path_prefix(self):
        """\\\\?\\C:\\... の長いパスの頭を付けても当たる。"""
        found = self.hits(("view_file", {"AbsolutePath": "\\\\?\\C:\\src\\local\\other\\x.py"}),
                          ("run_command", {"CommandLine": "type \\\\?\\c:\\SRC\\local\\other\\y.py"}),
                          ("view_file", {"AbsolutePath": "\\\\?\\C:\\src\\local\\own\\z.py"}))
        self.assertEqual(len(found), 2)

    def test_file_uri_in_an_argument(self):
        """引数の中の file:///C:/... の URI（%3A などの符号を含む）も、指すパスで当たる。"""
        found = self.hits(("run_command", {"CommandLine": "type file:///C:/src/local/other/x.py"}),
                          ("view_file", {"AbsolutePath": "file:///c%3A/src/local/other/y.py"}),
                          ("view_file", {"AbsolutePath": "file:///C:/src/local/own/z.py"}))
        self.assertEqual(len(found), 2)

    def test_deep_in_nested_json(self):
        """入れ子の引数の奥のパスと、文字列に詰めた JSON の中のパスも当たる。"""
        found = self.hits(("mcp_call", {"a": {"b": [{"c": ["x", {"d": "C:\\src\\local\\other\\x.py"}]}]}}),
                          ("mcp_call", {"Payload": json.dumps({"path": "C:\\src\\local\\other\\y.py", "n": [1]})}))
        self.assertEqual(len(found), 2)

    def test_dotdot_inside_an_absolute_path(self):
        """作業の場所の下に見せて .. で外へ出る絶対のパスも当たる。"""
        found = self.hits(("view_file", {"AbsolutePath": "C:\\src\\local\\own\\..\\other\\x.py"}),
                          ("run_command", {"CommandLine": "cat C:/src/local/own/sub/../../other/y.py"}),
                          ("view_file", {"AbsolutePath": "C:\\src\\local\\own\\sub\\..\\z.py"}))
        self.assertEqual(len(found), 2)


class HomeRelative(Logs):
    """ホームや環境変数から始まるパス。どのホームと見るか（実装役のアカウントか、検める側か）を決めかねるので残す。"""
    own, local = "C:\\Users\\u\\.local\\own", "C:\\Users\\u\\.local"

    def setUp(self):
        super().setUp()
        env = {"USERPROFILE": "C:\\Users\\u", "HOME": "C:\\Users\\u"}
        patcher = mock.patch.dict("os.environ", env)
        patcher.start()
        self.addCleanup(patcher.stop)

    @unittest.expectedFailure
    def test_userprofile_variable(self):
        self.assertTrue(self.hits(("run_command", {"CommandLine": "type %USERPROFILE%\\.local\\other\\x.py"})))

    @unittest.expectedFailure
    def test_home_variable(self):
        self.assertTrue(self.hits(("run_command", {"CommandLine": "cat $HOME/.local/other/x.py"})))

    @unittest.expectedFailure
    def test_tilde(self):
        self.assertTrue(self.hits(("view_file", {"AbsolutePath": "~/.local/other/x.py"})))


class NetworkToolNames(Logs):
    def test_names_outside_the_old_pattern(self):
        """ネットを見る道具の名が browser・_url・web を含まなくても（fetch・http_get など）、名指しは当たり、ほかは調べものに残る。"""
        for tool in ("fetch", "http_get", "HttpRequest", "curl", "wget"):
            with self.subTest(tool=tool):
                found, web = self.audit((tool, {"url": "https://github.com/tamaroulet/game-harness/tree/main/docs/bench"}),
                                        (tool, {"url": "https://mujoco.readthedocs.io/en/stable/XMLreference.html"}))
                self.assertEqual([f[1] for f in found], [tool])
                self.assertEqual([w[1] for w in web], [tool])

    @unittest.expectedFailure
    def test_network_through_a_shell_command(self):
        """端末の命令でネットを見る（curl でこのリポジトリを取る）手番。道具の名では分からず、命令の中身の決まりが要る。"""
        found, _ = self.audit(("run_command", {"CommandLine": "curl -s https://raw.githubusercontent.com/tamaroulet/game-harness/main/README.md"}))
        self.assertEqual(len(found), 1)


class PosixForms(Logs):
    """/ で始まるパスの形（一時ディレクトリの実のパス）。"""

    def setUp(self):
        super().setUp()
        self.local = self.tmp / "local"
        self.own, self.other = self.local / "own", self.local / "other"

    def test_json_string_with_a_slash_path(self):
        """文字列に詰めた JSON の中の、頭でない位置のパスも当たる。"""
        x = (self.other / "x.py").as_posix()
        found = self.hits(("mcp_call", {"Payload": json.dumps({"path": x})}))
        self.assertEqual(len(found), 1)

    def test_dotdot_inside_a_slash_path(self):
        found = self.hits(("run_command", {"CommandLine": f"cat {(self.own / 'sub').as_posix()}/../../other/x.py"}))
        self.assertEqual(len(found), 1)


class Leaks(unittest.TestCase):
    """漏れの走査：oracles のパス（区切りと大文字小文字を問わない）と canary の文字列。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.oracles = self.root / "or acles"
        (self.oracles / "bench" / "t1").mkdir(parents=True)
        self.cfg = {"oracles": str(self.oracles), "inbox": str(self.root / "inbox"), "out": str(self.root / "out")}
        self.canary("CANARY-7f3a9c")
        self.logs = self.root / "logs"
        self.logs.mkdir()

    def canary(self, text, encoding="utf-8"):
        (self.oracles / "bench" / "t1" / "canary.txt").write_bytes(text.encode(encoding))

    def scan(self, text, cfg=None):
        f = self.logs / "a.log"
        f.write_bytes(text.encode("utf-8"))
        return [n for _, n in bench_guard.leaks(cfg or self.cfg, [self.logs])]

    def test_path_mixed_case_and_separators(self):
        """oracles のパスの大文字小文字と区切りを 1 つの文字列の中で混ぜても当たる。"""
        mixed = "".join(c.upper() if i % 2 else c.lower() for i, c in enumerate(str(self.oracles)))
        mixed = mixed.replace("\\", "/").replace("/", "\\/", 1)
        self.assertEqual(self.scan(f"x\nread {mixed}/bench/t1\n"), [2])

    def test_canary_with_surrounding_whitespace_and_crlf(self):
        """canary.txt の前後の空白と CRLF、ログの行の前後の空白・タブ・CR があっても当たる。"""
        self.canary("\r\n  CANARY-7f3a9c\t\r\n")
        self.assertEqual(self.scan("a\r\n\t  CANARY-7f3a9c  \r\nb\rc CANARY-7f3a9c\n"), [2, 4])

    def test_canary_file_with_bom(self):
        """canary.txt が BOM 付き（Windows の道具が書いたもの）でも当たる。"""
        self.canary("\ufeffCANARY-7f3a9c\r\n")
        self.assertEqual(self.scan("ok\nsaw CANARY-7f3a9c\n"), [2])

    def test_config_value_with_surrounding_whitespace(self):
        """設定の oracles の値の前後に空白や改行があっても、パスも canary も当たる。"""
        cfg = {**self.cfg, "oracles": f"  {self.oracles}\n"}
        self.assertEqual(self.scan(f"saw CANARY-7f3a9c\nopen {self.oracles}/bench\n", cfg), [1, 2])

    def test_oracles_path_as_percent_encoded_uri(self):
        """oracles のパスを file の URI（空白は %20、ドライブの : は %3A）で書いても当たる。"""
        uri = "file:///" + quote(self.oracles.as_posix().lstrip("/"))
        self.assertIn("%20", uri)
        self.assertEqual(self.scan(f"x\n{{\"workspace_uris\": [\"{uri}/bench\"]}}\n"), [2])

    @unittest.expectedFailure
    def test_canary_split_across_lines(self):
        """改行で 2 行に分かれた canary（折り返し・stream の断片）。行ごとの決まりでは当たらない。行をまたいで当てるかを決めかねる。"""
        self.assertEqual(self.scan("saw CANARY-7f\n3a9c here\n"), [1])


if __name__ == "__main__":
    unittest.main()
