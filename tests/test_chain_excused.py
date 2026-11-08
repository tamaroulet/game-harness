"""鎖の採点で、外の中身に触れていないので当たりにしないもの 3 つ（v3 §15 の 11-06。L6 の新版で出た）：
開けなかった道（道具が「見つからない」で失敗した手番）・自分の会話の記録（agy のその会話の brain の下）・道具の書き出し
（作業ツリーの根の MUJOCO_LOG.TXT）。どれも段を無効にも 0 点にもせず、非公開の記録と索引に、あるときだけ別の欄で残す。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))
import bench_run  # noqa: E402
import chain_score  # noqa: E402

MARKS = ("B08", "examplechain", "docs/bench", "chain/s")
CONV = "0f1e2d3c-4b5a-4968-8776-655443322110"
OTHER = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
WIN_NOT_FOUND = "failed to read file: GetFileAttributesEx {p}: The system cannot find the file specified."


def write_log(path, cwd, steps, conv=CONV):
    """実装役の生ログ（要約・LOG_SPLIT・stream-json・LOG_SPLIT・標準エラー）。steps は (道具, 引数, 状態, output, error)。"""
    events = [json.dumps({"event": "init", "init": {"cwd": str(cwd)}, "conversation_id": conv})]
    for i, (tool, params, state, output, error) in enumerate(steps, 1):
        info = {"name": tool, "parameters": params}
        info.update({k: v for k, v in (("output", output), ("error", error)) if v is not None})
        events.append(json.dumps({"event": "step_update", "step_update": {
            "conversation_id": conv, "step_index": i, "state": state, "step_type": "tool", "tool_info": info}}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"s": 1}) + bench_run.LOG_SPLIT + "\n".join(events) + "\n" + bench_run.LOG_SPLIT + "e\n",
                    encoding="utf-8")
    return path


def view(path, state="DONE", output="20 lines, 400 bytes", error=None):
    return ("view_file", {"AbsolutePath": str(path)}, state, output, error)


def missing(path, message=None):
    return view(path, "ERROR", None, {"type": "TOOL_ERROR", "message": message or WIN_NOT_FOUND.format(p=path)})


class Audit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.local = Path(tmp.name) / "local"
        self.own = self.local / "bench" / "work" / "B08" / "factory-t-s4"
        self.own.mkdir(parents=True)
        self.brain = self.local / "bench" / ".gemini" / "antigravity-cli" / "brain"
        self.n = 0

    def audit(self, *steps, conv=CONV):
        self.n += 1
        log = write_log(self.own / f"implementer-0-{self.n}.log", self.own, steps, conv)
        return chain_score.audit_all([log], self.own, self.local, MARKS)

    def test_missing_file_outside_is_unopened_not_a_hit(self):
        """作業の場所の 1 つ上の、無いファイルを開こうとして「見つからない」で失敗した手番は、当たりにせず、開けなかった道に残す。"""
        p = self.local / "bench" / "work" / "B08" / "spec.md"
        for message in (None, f"open {p}: no such file or directory", f"file {p} does not exist"):
            with self.subTest(message=message):
                found, web, unopened, own = self.audit(missing(p, message))
                self.assertEqual(found, [])
                self.assertEqual(unopened, [(f"implementer-0-{self.n}.log", "view_file", str(p))])
                self.assertEqual((web, own), ([], []))

    def test_other_failures_and_opened_files_stay_hits(self):
        """中身が返った手番・ほかの理由の失敗・output のある失敗は、今のまま当たり。"""
        p = self.local / "bench" / "work" / "B08" / "spec.md"
        for step in (view(p), missing(p, "Access is denied."), view(p, "ERROR", "3 lines", {"message": "cannot find the file"})):
            with self.subTest(step=step[2:]):
                found, _, unopened, _ = self.audit(step)
                self.assertEqual(found, [(f"implementer-0-{self.n}.log", "view_file", str(p))])
                self.assertEqual(unopened, [])

    def test_own_conversation_record_is_not_a_hit(self):
        """その会話自身の記録の置き場の下（ウェブの調べものの中身の写しなど）は、開けても開けなくても当たりにせず、自分の会話の記録に残す。"""
        p = self.brain / CONV / ".system_generated" / "steps" / "44" / "content.md"
        for step in (view(p), view(p, "ERROR", None, {"message": "ContentOffset 38000 exceeds line range size 1547"})):
            with self.subTest(state=step[2]):
                found, web, unopened, own = self.audit(step)
                self.assertEqual(found, [])
                self.assertEqual(own, [(f"implementer-0-{self.n}.log", "view_file", str(p))])
                self.assertEqual((web, unopened), ([], []))

    def test_other_conversation_record_stays_a_hit(self):
        """ほかの会話の記録（ほかの走りの中身を含みうる）は今のまま当たり。会話の ID が記録に無いときは、どの会話の記録も当たり。"""
        other = self.brain / OTHER / ".system_generated" / "logs" / "overview.txt"
        own = self.brain / CONV / ".system_generated" / "steps" / "44" / "content.md"
        for path, conv in ((other, CONV), (own, None)):
            with self.subTest(conv=conv):
                found, _, _, mine = self.audit(view(path), conv=conv)
                self.assertEqual(found, [(f"implementer-0-{self.n}.log", "view_file", str(path))])
                self.assertEqual(mine, [])

    def test_own_record_ignores_case_and_separators_and_needs_the_brain_segment(self):
        convs = frozenset({CONV})
        self.assertTrue(chain_score.own_record(
            "D:\\work\\bench\\.gemini\\antigravity-cli\\brain\\" + CONV.upper() + "\\.system_generated\\steps\\44\\content.md", convs))
        self.assertTrue(chain_score.own_record(f"/home/bench/.gemini/antigravity-cli/brain/{CONV}", convs))
        self.assertFalse(chain_score.own_record(f"D:/work/bench/.gemini/antigravity-cli/brain/{OTHER}/x.md", convs))
        self.assertFalse(chain_score.own_record("D:/work/bench/.gemini/antigravity-cli/brain", convs))
        self.assertFalse(chain_score.own_record(f"D:/work/oracles/chains/B08/{CONV}/x.md", convs))
        self.assertFalse(chain_score.own_record(f"D:/x/.gemini/antigravity-cli/brain/{CONV}/y", frozenset()))

    def test_net_tool_pointing_at_own_record_is_a_lookup(self):
        """ネットの道具が自分の会話の記録を指しても、名指しが無ければ調べもの（無効にしない）。ほかの会話の記録を指せば当たり。"""
        mine = (self.brain / CONV / "steps" / "44" / "content.md").as_uri()
        other = (self.brain / OTHER / "steps" / "3" / "content.md").as_uri()
        for url, hits, lookups in ((mine, 0, 1), (other, 1, 0)):
            with self.subTest(url=url[-40:]):
                log = write_log(self.own / "implementer-0-8.log", self.own, [("read_url_content", {"Url": url}, "DONE", "ok", None)])
                found, web, _, _ = chain_score.audit_all([log], self.own, self.local, ("examplechain",))   # 一時の名に当たりうる土台の名は外す
                self.assertEqual((len(found), len(web)), (hits, lookups))

    def test_strays_and_audit_keep_their_shapes(self):
        """strays は当たりの列、audit は (当たり, 調べもの) のまま。どちらも開けなかった道と自分の会話の記録を含まない。"""
        p = self.local / "bench" / "work" / "B08" / "spec.md"
        log = write_log(self.own / "implementer-0-9.log", self.own,
                        [missing(p), view(self.brain / CONV / "a.md"), view(self.local / "other" / "b.md")])
        found, web = chain_score.audit([log], self.own, self.local, MARKS)
        self.assertEqual(found, [("implementer-0-9.log", "view_file", str(self.local / "other" / "b.md"))])
        self.assertEqual(chain_score.strays([log], self.own, self.local), found)
        self.assertEqual(web, [])


class Boundary(unittest.TestCase):
    def test_mujoco_log_at_the_root_is_an_artifact(self):
        self.assertEqual(chain_score.boundary(["MUJOCO_LOG.TXT", "pkg/a.py", "tests/test_pkg_x.py", "pkg/MUJOCO_LOG.TXT", ""], "pkg"),
                         ([], ["MUJOCO_LOG.TXT"]))

    def test_other_paths_stay_violations(self):
        names = ["docs/MUJOCO_LOG.TXT", "mujoco_log.txt", "notes.txt", "harness/x.py"]
        self.assertEqual(chain_score.boundary(names, "pkg"), (names, []))


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args], check=True, capture_output=True)


class FinishStage(unittest.TestCase):
    """finish_stage を通すと、当たりにしなかったものは非公開の記録と索引にだけ、あるときだけ別の欄で残り、点は 0 にならない。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.chains, self.out, self.priv, self.local = (self.root / n for n in ("chains", "out", "priv", "local"))
        self.priv.mkdir()
        for k, body in ((0, "    def test_s0(self):\n        self.assertGreaterEqual(f(), 1)\n"),
                        (1, "    def test_a(self):\n        self.assertEqual(f(), 2)\n")):
            name = "test_hidden_B07.py" if k == 0 else "test_chain_B07_s1.py"
            p = self.chains / "B07" / f"s{k}" / "hidden" / name
            p.parent.mkdir(parents=True)
            p.write_text("import unittest\nfrom pkg.mod import f\n\n\nclass T(unittest.TestCase):\n" + body, encoding="utf-8")
        self.rundir = bench_run.run_dir(self.out, "B07", "t-s1")
        (self.rundir / "git").mkdir(parents=True)
        self.repo = self.root / "repo"
        (self.repo / "pkg").mkdir(parents=True)
        git(self.repo, "init", "-q")
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 1\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "start")
        self.start = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()

    def finish(self, extra_files=(), steps=(), box=False):
        """box は箱の走りの印（採点の箱は呼ばず、試験の結果を差し替える）。"""
        if box:
            with patch.object(chain_score, "grade", return_value={"test_chain_B07_s1.T.test_a": "ok"}), \
                    patch.object(chain_score, "start_results", return_value=None):
                return self.finish_with(extra_files, steps, {"box": True})
        return self.finish_with(extra_files, steps, {})

    def finish_with(self, extra_files, steps, mark):
        (self.repo / "pkg" / "mod.py").write_text("def f(): return 2\n", encoding="utf-8")
        for name in extra_files:
            (self.repo / name).write_text("WARNING: Nan, Inf or huge value in QACC\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "tip")
        git(self.repo, "branch", "tip")
        git(self.repo, "bundle", "create", str(self.rundir / "git" / "run.bundle"), "tip")
        if steps:
            write_log(self.rundir / "items" / "w1" / "implementer-0-1.log", bench_run.work_dir(self.out, "B07", "t-s1"), steps)
        rec, st, row = {"base": "B07", "package": "pkg", **mark}, {"k": 1, "run_id": "t-s1", "start": self.start}, {}
        cfg = {"out": str(self.out), "oracles": str(self.chains.parent), "inbox": str(self.root / "inbox")}
        with patch.object(chain_score.machine, "path", return_value=self.local):
            chain_score.finish_stage(rec, st, row, self.out, self.chains, cfg, self.priv, {})
        return (row, json.loads((self.priv / "s1.json").read_text(encoding="utf-8")),
                (self.priv / "s1.index.md").read_text(encoding="utf-8"))

    def test_mujoco_log_keeps_the_score_and_is_recorded(self):
        row, priv, index = self.finish(extra_files=("MUJOCO_LOG.TXT",))
        self.assertEqual((row["violations"], row["new"]), (0, [1, 1]))
        self.assertEqual((priv["violations"], priv["artifacts"]), ([], ["MUJOCO_LOG.TXT"]))
        self.assertIn("## 道具の書き出し（範囲の外の変更に数えない）\n\n- MUJOCO_LOG.TXT\n", index)

    def test_other_root_file_still_zeroes_the_score(self):
        row, priv, _ = self.finish(extra_files=("notes.txt",))
        self.assertEqual((row["violations"], row["new"]), (1, [0, 1]))
        self.assertNotIn("artifacts", priv)

    def test_unopened_and_own_records_are_recorded_not_counted(self):
        p, mine = self.local / "x" / "spec.md", self.local / "bench" / ".gemini" / "antigravity-cli" / "brain" / CONV / "c.md"
        row, priv, index = self.finish(steps=[missing(p), view(mine)])
        self.assertEqual(row["strays"], 0)
        self.assertNotEqual(row.get("invalid"), "stray")
        self.assertEqual(priv["strays"], [])
        self.assertEqual(priv["unopened"], [["implementer-0-1.log", "view_file", str(p)]])
        self.assertEqual(priv["own_records"], [["implementer-0-1.log", "view_file", str(mine)]])
        self.assertIn("## 開けなかった道（無効にしない）\n\n- implementer-0-1.log：view_file — " + str(p), index)
        self.assertIn("## 自分の会話の記録（無効にしない）\n\n- implementer-0-1.log：view_file — " + str(mine), index)

    def rm_outside(self):
        """箱の中で、Windows のパスを名に持つフォルダを消すコマンド（v3 §15 の 11-08 その 3 で当たったものと同じ形）。"""
        p = self.local / "out" / "harness" / "selftest"
        return p, ("run_command", {"CommandLine": f'rm -rf "{p}"'}, "DONE", "", None)

    def test_box_run_paths_outside_are_recorded_not_counted(self):
        p, rm = self.rm_outside()
        row, priv, index = self.finish(steps=[rm], box=True)
        self.assertEqual(row["strays"], 0)
        self.assertNotEqual(row.get("invalid"), "stray")
        self.assertEqual(priv["strays"], [])
        self.assertEqual(priv["box_paths"], [["implementer-0-1.log", "run_command", str(p)]])
        self.assertIn("## 箱の中の外のパス（箱から届かない。無効にしない）\n\n- implementer-0-1.log：run_command — " + str(p), index)

    def test_the_same_path_in_a_windows_run_stays_a_hit(self):
        p, rm = self.rm_outside()
        row, priv, _ = self.finish(steps=[rm])
        self.assertEqual(row["strays"], 1)
        self.assertEqual(priv["strays"], [["implementer-0-1.log", "run_command", str(p)]])
        self.assertNotIn("box_paths", priv)

    def test_box_run_net_tool_naming_the_repo_stays_a_hit(self):
        url = ("read_url_content", {"Url": "https://github.com/tamaroulet/game-harness"}, "DONE", "ok", None)
        row, priv, _ = self.finish(steps=[url], box=True)
        self.assertEqual(row["strays"], 1)
        self.assertEqual(len(priv["strays"]), 1)
        self.assertNotIn("box_paths", priv)

    def test_nothing_excused_adds_no_keys_or_sections(self):
        """当たりにしなかったものが無ければ、非公開の記録の欄も索引の節も今までと同じ（採点し直しで、ファイルが変わらない）。"""
        _, priv, index = self.finish()
        self.assertEqual(list(priv), ["base", "k", "run_id", "results", "replaced", "violations", "strays", "web", "leaks", "at_start"])
        for _, title in chain_score.EXCUSED:
            self.assertNotIn(title, index)


if __name__ == "__main__":
    unittest.main()
