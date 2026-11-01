"""提案役の起こし方（harness/proposer_launch.py）に 2 つ以上の走りの記録（--tag の重ね）を渡す試験。
読む根・--add-dir・材料の索引・tag ごとの関所・重なりの終了コード 2・出た段の検め・記録の tags。
本物の CLI・記録は使わない。一時ディレクトリの偽の設定・記録・指示と、proc.run・resolve_cli の差し替えだけで書く。
"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "harness")]
import hline_base  # noqa: E402
import proposer_launch as pl  # noqa: E402
from test_proposer_launch import GOOD, NOW, Base, stream  # noqa: E402


class TwoTags(Base):
    def setUp(self):
        super().setUp()
        self.chains2 = self.out / "chains" / "v2"
        (self.chains2 / "B02").mkdir(parents=True)
        (self.chains2 / "B02" / "s3.json").write_text("{}", encoding="utf-8")
        (self.chains2 / "B02" / "s3.index.md").write_text("索引\n", encoding="utf-8")

    def launch2(self, result=None, tags=("v1", "v2"), extra=()):
        return self.launch(result=result, tag=list(tags), extra=extra, at=NOW)

    def test_both_records_are_roots_add_dirs_and_indexes_in_the_material(self):
        extra = self.tmp / "notes"
        rec, run = self.launch2(extra=[extra])
        args = run.call_args.args[0]
        dirs = [Path(args[i + 1]) for i, a in enumerate(args) if a == "--add-dir"]
        self.assertEqual(dirs, [self.chains, self.chains2, extra])
        self.assertEqual([Path(r) for r in rec["roots"]], [self.root, self.chains, self.chains2, extra])
        self.assertEqual(Path(run.call_args.args[1]), self.root)
        material = run.call_args.kwargs["input"].split("## 材料", 1)[1]
        self.assertIn("- tag: v1, v2", material)
        one, two = str(self.chains / "B01" / "s2.index.md"), str(self.chains2 / "B02" / "s3.index.md")
        self.assertIn(one, material)
        self.assertIn(two, material)
        self.assertLess(material.index(one), material.index(two))   # tag の並びの順

    def test_a_missing_record_for_one_tag_launches_nothing(self):
        for tags in (("v1", "v9"), ("v9", "v1")):
            with self.subTest(tags):
                rec, run = self.launch2(tags=tags)
                run.assert_not_called()
                self.assertFalse(rec["launched"])
                self.assertIn("開発の組の記録がありません", rec["skipped_reason"])
                self.assertIn("v9", rec["skipped_reason"])

    def test_a_root_overlapping_oracles_is_still_refused_with_two_tags(self):
        rec, run = self.launch2(extra=[self.oracles / "chains"])
        run.assert_not_called()
        self.assertIn("oracles と重なります", rec["skipped_reason"])

    def test_a_stage_in_only_one_of_the_records_passes_the_shape_check(self):
        text = GOOD.replace("- B01 s2\n", "- B01 s2\n- B02 s3\n")
        self.assertEqual(pl.shape_errors(text, [self.chains, self.chains2]), [])
        rec, _ = self.launch2(result=(0, stream(text=text), ""))
        self.assertEqual(rec["shape_errors"], [])
        self.assertTrue(pl.ok(rec))

    def test_a_stage_in_neither_record_does_not_pass(self):
        errs = pl.shape_errors(GOOD.replace("- B01 s2\n", "- B02 s2\n- B01 s3\n"), [self.chains, self.chains2])
        self.assertEqual(errs, ["## 出た段 の段の記録がありません: B02 s2", "## 出た段 の段の記録がありません: B01 s3"])
        self.assertEqual(pl.shape_errors(GOOD.replace("- B01 s2\n", "- B02 s3\n"), self.chains),
                         ["## 出た段 の段の記録がありません: B02 s3"])   # 1 本だけ渡したときは、その記録だけを見る

    def test_the_record_has_tags_as_a_list_even_for_one_tag(self):
        rec, _ = self.launch2()
        self.assertEqual(self.saved()["tags"], ["v1", "v2"])
        self.assertNotIn("tag", self.saved())
        rec, _ = self.launch(tag="v1")
        self.assertEqual((rec["tags"], self.saved()["tags"]), (["v1"], ["v1"]))


class MainTags(Base):
    def run_main(self, *tags):
        path = self.tmp / "hline.json"
        base = json.loads(hline_base.CONFIG.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**base, **self.cfg}), encoding="utf-8")
        agent = self.tmp / "proposer.json"
        agent.write_text(json.dumps(self.agent), encoding="utf-8")
        argv = [x for t in tags for x in ("--tag", t)] + ["--config", str(path)]
        with mock.patch.object(pl, "AGENT", agent), mock.patch.object(pl, "ROOT", self.root), \
                mock.patch.object(pl.proc, "resolve_cli", return_value=["claude"]), \
                mock.patch.object(pl.proc, "run", return_value=(0, stream(), "")) as run, \
                mock.patch("builtins.print"), contextlib.redirect_stderr(io.StringIO()):
            code = pl.main(argv)
        return code, run

    def test_two_tags_from_the_command_line(self):
        (self.out / "chains" / "v2").mkdir(parents=True)
        code, run = self.run_main("v1", "v2")
        self.assertEqual(code, 0)
        args = run.call_args.args[0]
        self.assertEqual([Path(args[i + 1]) for i, a in enumerate(args) if a == "--add-dir"],
                         [self.chains, self.out / "chains" / "v2"])

    def test_a_repeated_tag_does_nothing_and_returns_2(self):
        code, run = self.run_main("v1", "v1")
        self.assertEqual(code, 2)
        run.assert_not_called()
        self.assertFalse((self.out / "proposer").exists())


if __name__ == "__main__":
    unittest.main()
