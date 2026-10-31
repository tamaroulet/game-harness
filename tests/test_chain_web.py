"""鎖の道の検めの、ネットの道具の扱い（10-31 の L4 の後）。
引数がこのリポジトリか段の名を含むか、置き場の下で作業の場所の外を指すときだけ段を無効にし、
それ以外は「ネットの調べもの」として残す（無効にしない）。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import chain_score  # noqa: E402

MARKS = ("B08", "springchain", "docs/bench", "chain/s")


class Audit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = Path(tmp.name) / "local"
        self.own = self.local / "own"
        self.other = self.local / "other"
        for d in (self.own, self.other):
            d.mkdir(parents=True)
        self.n = 0

    def log(self, *steps):
        """実装役の生ログ：要約・LOG_SPLIT・stream-json（init と、道具の手番）・LOG_SPLIT・標準エラー。"""
        events = [json.dumps({"event": "init", "init": {"cwd": str(self.own)}})]
        for i, (tool, params) in enumerate(steps, 1):
            events.append(json.dumps({"event": "step_update", "step_update": {
                "step_index": i, "state": "DONE", "step_type": "tool", "tool_info": {"name": tool, "parameters": params}}}))
        self.n += 1
        p = self.own / f"implementer-0-{self.n}.log"
        p.write_text(json.dumps({"s": 1}) + bench_run.LOG_SPLIT + "\n".join(events) + "\n" + bench_run.LOG_SPLIT + "e\n",
                     encoding="utf-8")
        return p

    def audit(self, *steps):
        return chain_score.audit([self.log(*steps)], self.own, self.local, MARKS)

    def test_documentation_lookup_is_kept_not_invalid(self):
        """名指しの無い検索は調べものとして残り、当たりにならない。"""
        found, web = self.audit(("search_web", {"query": 'mujoco geom "zaxis" plane'}))
        self.assertEqual(found, [])
        self.assertEqual(web, [("implementer-0-1.log", "search_web", 'mujoco geom "zaxis" plane')])

    def test_lookup_naming_the_repository_is_invalid(self):
        """このリポジトリの名（持ち主・名前、大小文字を問わない）を含む検索は当たり。"""
        for query in ("site:github.com/tamaroulet/game-harness spec", "Game-Harness bench chain"):
            with self.subTest(query=query):
                found, web = self.audit(("search_web", {"query": query}))
                self.assertEqual(len(found), 1)
                self.assertIn("名指した", found[0][2])
                self.assertEqual(web, [])

    def test_lookup_naming_the_chain_is_invalid(self):
        """段の名（パッケージ・土台・仕様の置き場）を含む検索や URL は当たり。"""
        for params in ({"query": "springchain python package"}, {"Url": "https://example.com/docs/bench/B08/chain/s3/spec.md"}):
            with self.subTest(params=params):
                found, _ = self.audit(("read_url_content", params))
                self.assertEqual(len(found), 1)

    def test_network_tool_pointing_outside_the_work_place_is_invalid(self):
        """ネットの道具の引数が、置き場の下で作業の場所の外のパスを指せば当たり（パスの理由で）。"""
        found, web = self.audit(("read_url_content", {"Url": str(self.other / "notes.md")}))
        self.assertEqual([f[2] for f in found], [str(self.other / "notes.md")])
        self.assertEqual(web, [])

    def test_file_tools_are_checked_as_before(self):
        """ネットでない道具の道の検めは前と同じ：作業の場所の外は当たり、中は当たらない。"""
        found, web = self.audit(("view_file", {"AbsolutePath": str(self.other / "x.py")}),
                                ("view_file", {"AbsolutePath": str(self.own / "y.py")}))
        self.assertEqual([f[2] for f in found], [str(self.other / "x.py")])
        self.assertEqual(web, [])

    def test_index_lists_lookups_separately(self):
        """索引は、当たりと別の節に調べものを並べる。"""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "factory").mkdir()
            (Path(tmp) / "factory" / "queue.json").write_text(json.dumps({"items": {}}), encoding="utf-8")
            text = chain_score.index_text("B08", 2, "t-s2", Path(tmp), {}, [],
                                          [("implementer-0-1.log", "search_web", "mujoco condim")])
        at = text.index("## ネットの調べもの（無効にしない）")
        self.assertIn("- implementer-0-1.log：search_web — mujoco condim", text[at:])
        self.assertIn("## 道の検め\n\n- 無し", text)


if __name__ == "__main__":
    unittest.main()
