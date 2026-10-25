"""Argon 定性評価器のオラクル試験（from: director、総監督 47・48 本目）。

1. 1 つの命令で走る（引数はちょうど 2 つ、事前と事後が同じ入口、絞り込み引数なし、本物の置き場を汚さない）。
2. 全部を読む（main 写しと枝写しの全ファイルがモデル入力に入り、外（oracles/）は入らない）。
3. 事前と事後（事前: 文と子の文、事後: 受入または枝写し、モデル返信がそのまま記録に入る）。
4. 記録の形と判定の安全倒し（JSONL 記録、雛形や引用の混在は HIGH に倒す、呼べないとき・写し欠損時は落とす）。
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from harness import argon_eval
from harness.argon_eval import ArgonError
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

    def test_1_single_command_entrypoint_with_exact_two_args_and_no_leak(self):
        """1. 1 つの命令で走る（総監督 48 本目 §3-3）。
        - 引数がちょうど 2 つ（種類と対象）のときは一時 cfg を通して正常終了する
        - 引数が 2 つでないとき（余計な絞り込み引数など）は 1 で終わり、記録を書かない
        - 本物の置き場には一切書かない
        """
        evals_file = self.out_dir / "evals.jsonl"
        self.assertFalse(evals_file.exists())

        fake = self.fake_model("判定: LOW\n判断点: OK")

        # pre
        with patch.object(argon_eval, "_call_argon_model", return_value=("判定: LOW\n判断点: OK", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1})):
            ret_pre = argon_eval.main(["pre", "req.risk-tiers"], cfg=self.cfg)
            self.assertEqual(ret_pre, 0)

        # post
        with patch.object(argon_eval, "_call_argon_model", return_value=("判定: LOW\n判断点: OK", {"model": "m", "prompt_tokens": 1, "completion_tokens": 1, "seconds": 0.1})):
            ret_post = argon_eval.main(["post", "W-152"], cfg=self.cfg)
            self.assertEqual(ret_post, 0)

        # 一時置き場に 2 行記録されたこと
        lines = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)

        # 範囲を絞る引数や余計な引数があるときは 1 で終了し、記録が増えないこと
        ret_err = argon_eval.main(["pre", "req.risk-tiers", "--files-only"], cfg=self.cfg)
        self.assertEqual(ret_err, 1)

        ret_err_short = argon_eval.main(["pre"], cfg=self.cfg)
        self.assertEqual(ret_err_short, 1)

        # 行が増えていないこと
        lines_after = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines_after), 2)

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
        """4. 記録の形と判定の安全倒し（総監督 48 本目 §3-2）。
        - 正常判定は LOW/MEDIUM/HIGH
        - 雛形そのまま（判定: LOW | MEDIUM | HIGH）は HIGH に倒す
        - 引用の LOW と最後の HIGH がある応答は HIGH に倒す
        - 判定行が欠けている、または不正な場合も HIGH に倒す
        """
        evals_file = self.out_dir / "evals.jsonl"

        # 正常系 (LOW)
        model_low = self.fake_model("定性評価全文。\n判定: LOW\n判断点: 良好です")
        rec1 = argon_eval.evaluate("pre", "W-172", cfg=self.cfg, model_fn=model_low)
        self.assertEqual(rec1["decision"], "LOW")

        # 異常系 1: 雛形そのまま（値が 1 つでない） → HIGH
        model_template = self.fake_model("定性評価。\n判定: LOW | MEDIUM | HIGH\n判断点: テンプレート")
        rec2 = argon_eval.evaluate("post", "W-172", cfg=self.cfg, model_fn=model_template)
        self.assertEqual(rec2["decision"], "HIGH")

        # 異常系 2: 引用の LOW と最後の HIGH が混在（複数判定行） → HIGH
        model_quote = self.fake_model("過去の評価には\n判定: LOW\nとありましたが、再評価結果は\n判定: HIGH\n判断点: リスク発見")
        rec3 = argon_eval.evaluate("post", "301", cfg=self.cfg, model_fn=model_quote)
        self.assertEqual(rec3["decision"], "HIGH")

        # 異常系 3: 判定行なし → HIGH
        model_none = self.fake_model("感想文だけで判定行がありません。")
        rec4 = argon_eval.evaluate("post", "302", cfg=self.cfg, model_fn=model_none)
        self.assertEqual(rec4["decision"], "HIGH")

        # 記録された行の確認
        lines = evals_file.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 4)
        for line in lines:
            row = json.loads(line)
            self.assertIn("prompt_chars", row)
            self.assertIn("shas", row)
            self.assertEqual(row["shas"]["main"], "main_sha_123")

    def test_5_fails_when_evaluator_unconfigured_or_unreachable(self):
        """5. 本物の呼び出しが呼べないときは落とし、LOW を返さない（総監督 48 本目 §3-1）。"""
        evals_file = self.out_dir / "evals.jsonl"

        # evaluator 設定が無い cfg
        no_eval_cfg = dict(self.cfg)
        no_eval_cfg.pop("evaluator", None)

        ret = argon_eval.main(["pre", "req.risk-tiers"], cfg=no_eval_cfg)
        self.assertNotEqual(ret, 0)
        self.assertFalse(evals_file.exists())

        # 存在しない CLI を指定した cfg
        bad_cli_cfg = dict(self.cfg)
        bad_cli_cfg["evaluator"] = {"cli": "non_existent_command_xyz", "model": "m"}

        ret_bad = argon_eval.main(["pre", "req.risk-tiers"], cfg=bad_cli_cfg)
        self.assertNotEqual(ret_bad, 0)
        self.assertFalse(evals_file.exists())

    def test_6_fails_when_copies_missing_or_corrupt(self):
        """6. 写しが存在しない、または SHA が読めないときは落とし、記録を書かない（総監督 48 本目 §3-4）。"""
        evals_file = self.out_dir / "evals.jsonl"
        model = self.fake_model()

        # (a) mirror パスが存在しない
        broken_mirror_cfg = dict(self.cfg)
        broken_mirror_cfg["mirror"] = str(self.tmp / "non_existent_main")
        with self.assertRaises(ArgonError):
            argon_eval.evaluate("pre", "req.risk-tiers", cfg=broken_mirror_cfg, model_fn=model)
        self.assertFalse(evals_file.exists())

        # (b) .mirror.json が壊れていて SHA が読めない
        (self.mirror_dir / ".mirror.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ArgonError):
            argon_eval.evaluate("pre", "req.risk-tiers", cfg=self.cfg, model_fn=model)
        self.assertFalse(evals_file.exists())


if __name__ == "__main__":
    unittest.main()
