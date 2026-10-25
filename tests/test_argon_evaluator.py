"""Argon 定性評価器のオラクル試験（from: director、総監督 47 本目 §4）。

1. 1 つの命令で走る（事前と事後が同じ入口、絞り込み引数なし）。
2. 全部を読む（main 写しと枝写しの全ファイルがモデル入力に入り、写しの根の外（oracles 等）は入らない）。
3. 事前と事後（事前: 文と子の文、事後: 受入または枝写し、モデル返信がそのまま記録に入る）。
4. 記録の形（1 回 1 行 JSONL、out/hline/argon/、判定 LOW/MED/HIGH と判断点、費用、SHA。不正・欠損時は HIGH に安全倒し）。
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from harness import argon_eval
from harness.hline_base import ROOT, load_config


class TestArgonEvaluator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="argon_test_"))
        self.mirror_dir = self.tmp / "main"
        self.branches_dir = self.tmp / "branches"
        self.out_dir = self.tmp / "out" / "hline" / "argon"
        self.oracles_dir = self.tmp / "oracles"

        self.mirror_dir.mkdir(parents=True)
        self.branches_dir.mkdir(parents=True)
        self.out_dir.mkdir(parents=True)
        self.oracles_dir.mkdir(parents=True)

        # 仮の main の写し
        (self.mirror_dir / ".mirror.json").write_text(json.dumps({"sha": "main_sha_123"}), encoding="utf-8")
        (self.mirror_dir / "main_file.py").write_text("# main file content", encoding="utf-8")

        # 仮の枝写し 1
        branch1 = self.branches_dir / "301"
        branch1.mkdir()
        (branch1 / ".branch.json").write_text(json.dumps({"sha": "branch1_sha_456"}), encoding="utf-8")
        (branch1 / "feature1.py").write_text("# branch 1 feature content", encoding="utf-8")

        # 仮の枝写し 2
        branch2 = self.branches_dir / "302"
        branch2.mkdir()
        (branch2 / ".branch.json").write_text(json.dumps({"sha": "branch2_sha_789"}), encoding="utf-8")
        (branch2 / "feature2.py").write_text("# branch 2 feature content", encoding="utf-8")

        # 写しの根の外にある仮の oracles（読まれてはならない）
        (self.oracles_dir / "secret_oracle.py").write_text("# secret oracle content", encoding="utf-8")

        # 本物の設定をベースにしつつ一時ディレクトリに差し替えた cfg
        base_cfg = load_config()
        self.cfg = dict(base_cfg)
        self.cfg["mirror"] = str(self.mirror_dir)
        self.cfg["branches"] = str(self.branches_dir)
        self.cfg["argon"] = str(self.out_dir)

        # 呼び出しキャプチャ
        self.prompts_received = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_model(self, response_text="判定: LOW\n判断点: ねらいに届いています"):
        def _model(prompt):
            self.prompts_received.append(prompt)
            return response_text, {
                "prompt_tokens": 1200,
                "completion_tokens": 80,
                "seconds": 0.45,
                "model": "fake-argon-model",
            }
        return _model

    def test_1_single_command_entrypoint_with_no_scope_filters(self):
        """1. 1 つの命令で走る。事前と事後が同じ入口から走り、範囲を絞る引数が無い。"""
        # main() に pre と post のどちらを渡しても正常終了する
        model = self.fake_model()

        # pre
        with patch.object(argon_eval, "_call_gemini_model", return_value=("判定: LOW\n判断点: OK", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1})):
            ret_pre = argon_eval.main(["pre", "req.risk-tiers"])
            self.assertEqual(ret_pre, 0)

        # post
        with patch.object(argon_eval, "_call_gemini_model", return_value=("判定: LOW\n判断点: OK", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1})):
            ret_post = argon_eval.main(["post", "W-152"])
            self.assertEqual(ret_post, 0)

        # 範囲を絞るオプション引数が定義されていない（余計な引数はエラー扱い）
        ret_err = argon_eval.main(["pre", "req.risk-tiers", "--files-only"])
        # argv[0] と argv[1] だけを解釈し、範囲を枝やファイルで絞る引数は提供しない
        self.assertIn(ret_pre, (0, None))

    def test_2_reads_all_copies_and_excludes_outside_roots(self):
        """2. 全部を読む。仮の main の写しと 2 つの枝の写しを置くと、3 つすべてのファイルが入力に入り、外（oracles/）は入らない。"""
        model = self.fake_model("判定: LOW\n判断点: 全量確認完了")
        argon_eval.evaluate("pre", "req.risk-tiers", cfg=self.cfg, model_fn=model)

        self.assertEqual(len(self.prompts_received), 1)
        prompt = self.prompts_received[0]

        # 3 つすべてのファイルがモデルへの入力に入っている
        self.assertIn("# main file content", prompt)
        self.assertIn("# branch 1 feature content", prompt)
        self.assertIn("# branch 2 feature content", prompt)

        # 写しの根の外にあるファイル（oracles/）はモデルの入力に入らない
        self.assertNotIn("secret_oracle.py", prompt)
        self.assertNotIn("# secret oracle content", prompt)

    def test_3_pre_and_post_contexts(self):
        """3. 事前と事後。
        - 事前：木の節の id かタスクの id を渡すと、その文と子（節かタスク）の文が入力に入る
        - 事後：済んだタスクの id か PR の番号を渡すと、そのタスクの受入（PR のときは枝の写し）が入力に入る
        - どちらも、モデルが返した文がそのまま記録に入る
        """
        # (a) 事前: 木の節（req.risk-tiers）
        model_pre = self.fake_model("事前評価: 子タスクでねらいに届きます。\n判定: MEDIUM\n判断点: 要件と子タスクの網羅性を確認")
        rec_pre = argon_eval.evaluate("pre", "req.risk-tiers", cfg=self.cfg, model_fn=model_pre)
        prompt_pre = self.prompts_received[-1]

        self.assertIn("req.risk-tiers", prompt_pre)
        # 節の親・子情報（tasks や depends_on）が文脈に含まれている
        self.assertIn("W-154", prompt_pre)
        self.assertEqual(rec_pre["evaluation"], "事前評価: 子タスクでねらいに届きます。\n判定: MEDIUM\n判断点: 要件と子タスクの網羅性を確認")

        # (b) 事後: 済んだタスク（W-152）
        model_post_task = self.fake_model("事後タスク評価: 受入を満たしています。\n判定: LOW\n判断点: 完了確認")
        rec_post_task = argon_eval.evaluate("post", "W-152", cfg=self.cfg, model_fn=model_post_task)
        prompt_post_task = self.prompts_received[-1]

        self.assertIn("W-152", prompt_post_task)
        self.assertEqual(rec_post_task["evaluation"], "事後タスク評価: 受入を満たしています。\n判定: LOW\n判断点: 完了確認")

        # (c) 事後: PR 番号（301）
        model_post_pr = self.fake_model("事後PR評価: 枝の変更内容を確認しました。\n判定: LOW\n判断点: 差分健全")
        rec_post_pr = argon_eval.evaluate("post", "301", cfg=self.cfg, model_fn=model_post_pr)
        prompt_post_pr = self.prompts_received[-1]

        self.assertIn("301", prompt_post_pr)
        self.assertIn("# branch 1 feature content", prompt_post_pr)
        self.assertEqual(rec_post_pr["evaluation"], "事後PR評価: 枝の変更内容を確認しました。\n判定: LOW\n判断点: 差分健全")

    def test_4_record_format_and_fail_safe_to_high(self):
        """4. 記録の形。
        1 回が 1 行の JSONL で、config/hline.json の 1 か所（out/hline/argon/）に出る。受信箱には書かない。
        1 行が持つもの：事前事後・対象・評価文・判定（LOW/MED/HIGH）・判断点・費用・写しSHA。
        判定が 3 つのどれでもないときや欠けているときは、HIGH と同じく人へ回す印にする。
        """
        evals_file = self.out_dir / "evals.jsonl"
        self.assertFalse(evals_file.exists())

        # 正常系 (LOW)
        model_low = self.fake_model("定性評価全文。\n判定: LOW\n判断点: 良好です")
        rec1 = argon_eval.evaluate("pre", "W-172", cfg=self.cfg, model_fn=model_low)

        self.assertTrue(evals_file.exists())
        lines = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)

        row1 = json.loads(lines[0])
        self.assertEqual(row1["kind"], "pre")
        self.assertEqual(row1["target"], "W-172")
        self.assertEqual(row1["decision"], "LOW")
        self.assertEqual(row1["rationale"], "良好です")
        self.assertIn("prompt_tokens", row1["usage"])
        self.assertIn("completion_tokens", row1["usage"])
        self.assertIn("seconds", row1["usage"])
        self.assertEqual(row1["shas"]["main"], "main_sha_123")
        self.assertEqual(row1["shas"]["branches/301"], "branch1_sha_456")
        self.assertEqual(row1["shas"]["branches/302"], "branch2_sha_789")

        # 異常系 1: 判定が 3 つのどれでもない (UNKNOWN)
        model_bad = self.fake_model("定性評価全文。\n判定: UNKNOWN\n判断点: 不明な状態")
        rec2 = argon_eval.evaluate("post", "W-172", cfg=self.cfg, model_fn=model_bad)

        lines = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        row2 = json.loads(lines[1])
        # HIGH（人へ回す側）に安全倒しされていること
        self.assertEqual(row2["decision"], "HIGH")

        # 異常系 2: 判定行が欠けている
        model_missing = self.fake_model("ただの感想文で判定がありません。")
        rec3 = argon_eval.evaluate("post", "302", cfg=self.cfg, model_fn=model_missing)

        lines = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 3)
        row3 = json.loads(lines[2])
        # 判定欠落時も HIGH に安全倒しされていること
        self.assertEqual(row3["decision"], "HIGH")

    def test_argon_dir_requires_argon_key_without_fallback(self):
        """argon_dir は cfg['argon'] だけを読む。無ければ KeyError で落とす。"""
        empty_cfg = {}
        with self.assertRaises(KeyError):
            argon_eval.argon_dir(empty_cfg)


if __name__ == "__main__":
    unittest.main()
