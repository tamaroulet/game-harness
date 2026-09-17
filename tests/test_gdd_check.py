"""網羅検査器（harness/gdd_check.py、docs/design/spec_pipeline.md §4〜§6）。

    python -m unittest discover -s tests -v

土台は「錨をすべて満たし、仮も質問も無い」合格かつマージ可の spec。1 か所ずつ壊して、
不合格（spec の不備）になるのか、合格のままマージ不可（GDD の不足）になるのかを確かめる。
"""
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import gdd_check  # noqa: E402
import project  # noqa: E402

gdd_check.CFG = gdd_check.load_config()

GDD = """<!-- project: falling-blocks -->
<!-- version: 2 -->
# 仕様

## 1. 盤面
- 幅 10 × 高さ 20。
- 1 ティックは 1/60 秒。時間は整数ティックで進める。

## 2. 操作
- 回転は 90 度。壁キック等の位置補正は行わない。

```
乱数は XorShift32。シードを注入できる。
```

---
| 形状 | 座標 |
|:--|:--|
| 各形状 | 座標表に従う |
"""
# 行番号: 1-2 メタ / 3 見出し / 4 空 / 5 見出し / 6 / 7 / 8 空 / 9 見出し / 10 / 11 空 / 12 フェンス / 13 中身 / 14 フェンス
#         15 空 / 16 水平線 / 17 表の見出し行 / 18 区切り / 19 表の行
SHAPES = ["I", "O", "T", "S", "Z", "J", "L"]
ROTS = ["0", "R", "2", "L"]


def meta(text=GDD, project="falling-blocks", version=2):
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"<!-- project: {project} -->\n<!-- gdd-version: {version} -->\n<!-- gdd-sha256: {sha} -->\n"


def pr_rows():
    rows, n = [], 1
    for s in SHAPES:
        for r in ROTS:
            rows.append(f"| PR-{n:02d} | 形状 {s} 向き {r} | (0, 0) (1, 0) (2, 0) (3, 0) | 確定 | L17-L19 |")
            n += 1
        rows.append(f"| PR-{n:02d} | 形状 {s} 回転原点 | (1.5, 0.5) | 確定 | L17-L19 |")
        n += 1
    rows.append(f"| PR-{n:02d} | 盤面の幅 | 10 | 確定 | L6 |")
    return rows


def spec(lp=None, st=None, rl=None, if_=None, pr=None, hc=None, head=None):
    lp = lp or ["| LP-01 | 盤面は幅 10 × 高さ 20 | L6 |"]
    st = st or ["| ST-01 | 待機 | 開始 | 進行中 | L7 |"]
    rl = rl or ["| RL-01 | 1 ティックごとに進める | 整数ティック | PR-36 | L7 |",
                "| RL-02 | 回転は 90 度。壁キック等の位置補正は行わない | - | - | L10 |",
                "| RL-03 | 乱数は XorShift32 | - | - | L13 |",
                "| RL-04 | 不変条件: 消去ライン数 × 10 ＋ 盤面のブロック数 ＝ 4 × ロック数 | - | - | L6 |"]
    if_ = if_ or ["| IF-01 | n ティック進める | n は非負の整数 | L7 |",
                  "| IF-02 | シードを注入する | 32 ビット符号なし整数 | L13 |"]
    pr = pr or pr_rows()
    hc = hc or ["| （該当なし） | - | - |"]
    parts = [head if head is not None else meta(), "# 構造化仕様\n"]
    tables = [("## 1. ループと終了条件", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", lp),
              ("## 2. 状態遷移", "| ID | 状態 | 契機 | 遷移先 | 根拠 |", "|:--|:--|:--|:--|:--|", st),
              ("## 3. 規則と計算式", "| ID | 規則・式 | 境界値 | 参照パラメーター | 根拠 |", "|:--|:--|:--|:--|:--|", rl),
              ("## 4. 公開インターフェース", "| ID | 公開する状態・操作 | 型・範囲 | 根拠 |", "|:--|:--|:--|:--|", if_),
              ("## 5. 外部パラメーター", "| ID | 名前 | 値 | 区分 | 根拠 |", "|:--|:--|:--|:--|:--|", pr),
              ("## 6. 人間確認・演出", "| ID | 内容 | 根拠 |", "|:--|:--|:--|", hc)]
    for h, header, sep, rows in tables:
        parts.append(f"\n{h}\n\n{header}\n{sep}\n" + "\n".join(rows) + "\n")
    return "".join(parts)


def questions(rows=None, head=None):
    rows = rows or ["| （該当なし） | - | - | - |"]
    return ((head if head is not None else meta()) + "\n## 質問\n\n| ID | 質問 | 根拠 | 推測で決めない理由 |\n"
            "|:--|:--|:--|:--|\n" + "\n".join(rows) + "\n")


TERMS = [("ハードドロップ", "ハードドロップ"), ("ホールド", "hold"), ("SRS", "壁キック"), ("SRS", "SRS")]


def run(spec_text=None, q_text=None, gdd=GDD, terms=TERMS, prev=None):
    return gdd_check.check(gdd, spec_text if spec_text is not None else spec(),
                           q_text if q_text is not None else questions(), terms, prev)


class Base(unittest.TestCase):
    def assertNG(self, result, fragment):
        joined = "\n".join(result["problems"])
        self.assertIn(fragment, joined)
        self.assertFalse(result["summary"]["passed"])
        self.assertFalse(result["summary"]["mergeable"])

    def assertPassedNotMergeable(self, result):
        self.assertEqual(result["problems"], [])
        self.assertTrue(result["summary"]["passed"])
        self.assertFalse(result["summary"]["mergeable"])


class BaselineTests(Base):
    def test_complete_spec_passes_and_is_mergeable(self):
        r = run()
        self.assertEqual(r["problems"], [])
        self.assertTrue(r["summary"]["mergeable"])
        self.assertEqual(r["summary"]["counts"]["PR"], 36)
        self.assertEqual(r["summary"]["anchors_missing"], [])


class ExcludeRuleTests(Base):
    def test_every_rule_example_in_config(self):
        cfg, _ = gdd_check.CFG
        for rule in cfg["exclude_rules"]:
            rx = re.compile(rule["regex"])
            for s in rule["examples"]["match"]:
                with self.subTest(rule=rule["name"], match=s):
                    self.assertTrue(rx.match(s))
            for s in rule["examples"]["no_match"]:
                with self.subTest(rule=rule["name"], no_match=s):
                    self.assertFalse(rx.match(s))

    def test_denominator_and_counts_per_rule(self):
        lines, targets, excluded = gdd_check.gdd_lines(GDD)
        self.assertEqual(sorted(targets), [6, 7, 10, 13, 17, 19])
        self.assertEqual(excluded, {"blank": 4, "heading": 3, "horizontal_rule": 1, "code_fence": 2,
                                    "table_separator": 1, "html_comment": 2, "bullet_only": 0})

    def test_fence_content_is_not_excluded_even_if_it_looks_like_a_heading(self):
        text = GDD.replace("乱数は XorShift32。シードを注入できる。", "# これはコードの中")
        self.assertIn(13, gdd_check.gdd_lines(text)[1])


class StructureTests(Base):
    def test_metadata_must_match_gdd(self):
        self.assertNG(run(spec(head=meta(version=1))), "gdd-version が GDD と違います")
        self.assertNG(run(spec(head=meta(text=GDD + "x"))), "gdd-sha256 が GDD")
        self.assertNG(run(spec(head="<!-- project: falling-blocks -->\n")), "メタデータ")

    def test_section_order_and_columns(self):
        swapped = spec().replace("## 1. ループと終了条件", "## X").replace("## 2. 状態遷移", "## 1. ループと終了条件") \
                        .replace("## X", "## 2. 状態遷移")
        self.assertNG(run(swapped), "節の見出しと順序")
        self.assertNG(run(spec().replace("| ID | 内容 | 根拠 |", "| ID | 本文 | 根拠 |", 1)), "表の列が")
        self.assertNG(run(spec(lp=["| LP-01 | 列が足りない |"])), "列の数が")
        self.assertNG(run(spec().replace("# 構造化仕様", "前置きの本文")), "最初の節より前に本文")

    def test_ids(self):
        self.assertNG(run(spec(lp=["| LP-1 | 桁が足りない | L6 |"])), "LP-nn の形ではありません")
        self.assertNG(run(spec(lp=["| RL-09 | 節と接頭辞が違う | L6 |"])), "LP-nn の形ではありません")
        self.assertNG(run(spec(lp=["| LP-01 | a | L6 |", "| LP-01 | b | L7 |"])), "ID が重複")

    def test_none_row_must_be_alone(self):
        self.assertNG(run(spec(hc=["| （該当なし） | - | - |", "| HC-01 | 手触り | L10 |"])), "一緒に置けません")


class RefTests(Base):
    def test_ref_format_range_and_targets(self):
        self.assertNG(run(spec(lp=["| LP-01 | a | 6 |"])), "根拠の形")
        self.assertNG(run(spec(lp=["| LP-01 | a | L6,L7 |"])), "根拠の形")
        self.assertNG(run(spec(lp=["| LP-01 | a | L99 |"])), "範囲")
        self.assertNG(run(spec(lp=["| LP-01 | a | L7-L6 |"])), "逆順")
        self.assertNG(run(spec(lp=["| LP-01 | a | L5 |"])), "分母から外した行")
        self.assertNG(run(spec(lp=["| LP-01 | a | - |"])), "根拠がありません")

    def test_range_counts_for_coverage(self):
        r = run(spec(lp=["| LP-01 | 盤面 | L5-L7 |"]))
        self.assertEqual(r["problems"], [])


EXTRA = GDD + "- ネクストは 3 個表示する。\n"   # L20（どの行にも引かれていない対象行）


class CoverageTests(Base):
    def test_uncited_target_line_is_reported_with_text(self):
        r = run(spec(head=meta(text=EXTRA)), questions(head=meta(text=EXTRA)), gdd=EXTRA)
        self.assertNG(r, "網羅: GDD の L20")
        self.assertIn("ネクストは 3 個表示する", "\n".join(r["problems"]))

    def test_questions_count_toward_coverage(self):
        q = questions(["| Q-01 | ネクストは何個まで出せるか | L20 | GDD に上限が無い |"], head=meta(text=EXTRA))
        r = run(spec(head=meta(text=EXTRA)), q, gdd=EXTRA)
        self.assertPassedNotMergeable(r)
        self.assertEqual(r["summary"]["questions"][0]["headings"], ["# 仕様", "## 2. 操作"])


class ReferenceTests(Base):
    def test_unknown_ids_in_cells(self):
        self.assertNG(run(spec(lp=["| LP-01 | PR-99 を使う | L6 |"])), "実在しない ID PR-99")
        self.assertNG(run(spec(lp=["| LP-01 | Q-01 を見る | L6 |"])), "実在しない ID Q-01")

    def test_param_column_and_category(self):
        rl = ["| RL-01 | 1 ティックごと | - | IF-01 | L7 |", "| RL-02 | XorShift32 | - | - | L13 |",
              "| RL-03 | 不変条件: a | - | - | L10 |"]
        self.assertNG(run(spec(rl=rl)), "参照パラメーターは PR-nn")
        rows = pr_rows()
        rows[-1] = rows[-1].replace("| 確定 |", "| たぶん |")
        self.assertNG(run(spec(pr=rows)), "区分は 確定 か 仮")


class MergeabilityTests(Base):
    def test_provisional_value_blocks_merge_but_passes(self):
        rows = pr_rows()
        rows[-1] = rows[-1].replace("| 確定 |", "| 仮 |")
        r = run(spec(pr=rows))
        self.assertPassedNotMergeable(r)
        self.assertEqual(r["summary"]["provisional"][0]["name"], "盤面の幅")

    def test_question_blocks_merge_but_passes(self):
        r = run(q_text=questions(["| Q-01 | ソフトドロップは何倍か | L7 | GDD に値が無い |"]))
        self.assertPassedNotMergeable(r)


class TermTests(Base):
    def test_forbidden_term_not_in_cited_gdd_line(self):
        self.assertNG(run(spec(lp=["| LP-01 | ハードドロップで即座に固定 | L6 |", "| LP-02 | 高さ | L7 |"])),
                      "実験用の機能（ハードドロップ）")

    def test_term_allowed_when_cited_gdd_line_contains_it(self):
        self.assertEqual(run()["problems"], [])   # RL-02 は L10 の「壁キック」を引いている

    def test_same_term_citing_another_line_is_refused(self):
        rl = ["| RL-01 | 1 ティックごとに進める | 整数ティック | PR-36 | L7 |",
              "| RL-02 | 回転は 90 度。壁キック等の位置補正は行わない | - | - | L6, L10 |".replace("L6, L10", "L7"),
              "| RL-03 | 乱数は XorShift32 | - | - | L13 |",
              "| RL-04 | 不変条件: x | - | - | L10 |"]
        self.assertNG(run(spec(rl=rl)), "（SRS）の語「壁キック」")

    def test_ascii_terms_use_word_boundaries_and_ignore_case(self):
        self.assertNG(run(spec(lp=["| LP-01 | HOLD a piece | L6 |", "| LP-02 | x | L7 |"])), "語「hold」")
        self.assertEqual(run(spec(lp=["| LP-01 | threshold と holder | L6 |", "| LP-02 | x | L7 |"]))["problems"], [])

    def test_terms_apply_to_questions(self):
        self.assertNG(run(q_text=questions(["| Q-01 | SRS を入れますか | L10 | 無い |"])), "語「SRS」")

    def test_candidates_are_reported_not_judged(self):
        r = run(spec(lp=["| LP-01 | スピンボーナス | L6 |", "| LP-02 | x | L7 |"]))
        self.assertEqual(r["problems"], [])
        self.assertIn("スピンボーナス", r["summary"]["term_candidates"])


class HumanCheckTests(Base):
    def test_logic_in_hc_is_refused(self):
        for content in ("消去時に 4 列なら派手", "得点 > 1000 で演出", "RL-01 の演出", "30 ティック光る"):
            with self.subTest(content):
                self.assertNG(run(spec(hc=[f"| HC-01 | {content} | L10 |"])), "人間確認にロジック")

    def test_pure_presentation_is_allowed(self):
        r = run(spec(hc=["| HC-01 | 回転したときの手触りが気持ちよい | L10 |"]))
        self.assertEqual(r["problems"], [])
        self.assertEqual(r["summary"]["human_checks"][0]["id"], "HC-01")


class AnchorTests(Base):
    def test_missing_ticks_is_a_gdd_shortfall(self):
        rl = ["| RL-01 | 1 フレームごとに進める | - | PR-36 | L7 |", "| RL-02 | 壁キック等の位置補正は行わない | - | - | L10 |",
              "| RL-03 | XorShift32 | - | - | L13 |", "| RL-04 | 不変条件: x | - | - | L6 |"]
        r = run(spec(rl=rl))
        self.assertPassedNotMergeable(r)
        self.assertTrue(any(a.startswith("A1") for a in r["summary"]["anchors_missing"]))

    def test_seconds_in_interface_is_refused(self):
        self.assertNG(run(spec(if_=["| IF-01 | ティックを秒で進める | float | L7 |",
                                    "| IF-02 | シードを注入する | 整数 | L13 |"])), "秒・浮動小数の時間")

    def test_shape_table_must_be_complete(self):
        r = run(spec(pr=pr_rows()[1:]))   # I の向き 0 が無い
        self.assertPassedNotMergeable(r)
        self.assertIn("I", next(a for a in r["summary"]["anchors_missing"] if a.startswith("A2")))

    def test_shape_values_must_be_four_distinct_int_pairs(self):
        for bad in ("(0, 0) (1, 0) (2, 0)", "(0, 0) (0, 0) (1, 0) (2, 0)", "(0, 0) (1, 0) (2, 0) (3.5, 0)",
                    "(0, 0) (1, 0) (2, 0) (3, 0) 余計"):
            rows = pr_rows()
            rows[0] = f"| PR-01 | 形状 I 向き 0 | {bad} | 確定 | L19 |"
            with self.subTest(bad):
                self.assertNG(run(spec(pr=rows)), "異なる 4 組")
        rows = pr_rows()
        rows[4] = "| PR-05 | 形状 I 回転原点 | (1.25, 0) | 確定 | L19 |"
        self.assertNG(run(spec(pr=rows)), "回転原点は")
        rows = pr_rows()
        rows[0] = "| PR-01 | 形状 X 向き 0 | (0, 0) (1, 0) (2, 0) (3, 0) | 確定 | L19 |"
        self.assertNG(run(spec(pr=rows)), "形状の名前が不正")

    def test_seed_and_xorshift(self):
        r = run(spec(if_=["| IF-01 | n ティック進める | 整数 | L7 |", "| IF-02 | 初期化する | - | L13 |"]))
        self.assertPassedNotMergeable(r)
        self.assertTrue(any(a.startswith("A3") for a in r["summary"]["anchors_missing"]))
        self.assertNG(run(spec(lp=["| LP-01 | System.Random で並べる | L6 |", "| LP-02 | x | L7 |"])), "System.Random")

    def test_invariant(self):
        rl = ["| RL-01 | 1 ティックごと | - | PR-36 | L7 |", "| RL-02 | 壁キック等の位置補正は行わない | - | - | L10 |",
              "| RL-03 | XorShift32 | - | - | L13 |", "| RL-04 | 保存則として a | - | - | L6 |"]
        r = run(spec(rl=rl))
        self.assertPassedNotMergeable(r)
        self.assertTrue(any(a.startswith("A4") for a in r["summary"]["anchors_missing"]))


class ContinuityTests(Base):
    def test_kept_changed_removed_added(self):
        prev = spec(lp=["| LP-01 | 盤面は幅 10 × 高さ 20 | L6 |", "| LP-02 | 消える行 | L7 |"])
        cur = spec(lp=["| LP-01 | 盤面は幅 10 × 高さ 22 | L6 |", "| LP-03 | 新しい行 | L7 |"])
        c = run(cur, prev=prev)
        self.assertEqual(c["problems"], [])
        ch = c["summary"]["id_changes"]
        self.assertEqual((ch["changed"], ch["removed"], ch["added"]), (["LP-01"], ["LP-02"], ["LP-03"]))
        self.assertIn("RL-01", ch["kept"])

    def test_refs_moving_is_not_a_change(self):
        prev = spec(lp=["| LP-01 | 盤面は幅 10 × 高さ 20 | L7 |"])
        self.assertEqual(run(spec(lp=["| LP-01 | 盤面は幅 10 × 高さ 20 | L6-L7 |"]), prev=prev)
                         ["summary"]["id_changes"]["changed"], [])

    def test_reusing_a_removed_number_is_refused(self):
        prev = spec(lp=["| LP-01 | a | L6 |", "| LP-03 | c | L7 |"])        # LP-02 は過去に消えた番号
        reused = spec(lp=["| LP-01 | a | L6 |", "| LP-02 | 新しい行 | L7 |"])
        self.assertNG(run(reused, prev=prev), "最大番号")
        fresh = spec(lp=["| LP-01 | a | L6 |", "| LP-04 | 新しい行 | L7 |"])
        self.assertEqual(run(fresh, prev=prev)["problems"], [])

    def test_unreadable_previous_spec_is_an_environment_error(self):
        with self.assertRaises(gdd_check.ConfigError):
            run(prev="壊れた spec")


class PrecheckTests(unittest.TestCase):
    """LLM を呼ぶ前の錨の事前検査（B-2d）。"""
    FULL = ("1 ティックは 1/60 秒。乱数は XorShift32、シードを注入する。不変条件: a\n"
            + "".join(f"形状 {s} 向き {r}: (0, 0) (1, 0) (2, 0) (3, 0)\n" for s in "IOTSZJL" for r in "0R2L"))

    def test_complete_gdd_has_nothing_missing(self):
        self.assertEqual(gdd_check.precheck(self.FULL), [])

    def test_each_word_is_required(self):
        for word in ("ティック", "XorShift32", "シード", "不変条件"):
            with self.subTest(word):
                missing = gdd_check.precheck(self.FULL.replace(word, "＿"))
                self.assertEqual(missing, [f"「{word}」の記述が GDD にありません"])

    def test_coordinate_pairs_must_reach_the_minimum(self):
        short = self.FULL.replace("(3, 0)\n", "\n", 1)   # 1 組だけ減らす
        self.assertEqual(len(gdd_check.precheck(short)), 1)
        self.assertIn("111 組しかありません", gdd_check.precheck(short)[0])


class ProjectTermsTests(unittest.TestCase):
    def test_falling_blocks_terms_cover_the_experiment_features(self):
        features = {f for f, _ in gdd_check.load_terms("falling-blocks")}
        self.assertEqual(features, {"ハードドロップ", "ゴースト", "ホールド", "SRS"})

    def test_games_without_terms_file_have_none(self):
        self.assertEqual(gdd_check.load_terms("unity-2d"), [])


class CliTests(unittest.TestCase):
    def test_exit_codes_and_summary_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            files = {"gdd": GDD, "spec": spec(), "questions": questions()}
            for k, v in files.items():
                (d / f"{k}.md").write_bytes(v.encode("utf-8"))
            args = ["--project", "falling-blocks", "--gdd", str(d / "gdd.md"), "--spec", str(d / "spec.md"),
                    "--questions", str(d / "questions.md"), "--summary", str(d / "s.json")]
            self.assertEqual(gdd_check.main(args), 0)
            self.assertTrue(json.loads((d / "s.json").read_text(encoding="utf-8"))["summary"]["mergeable"])
            (d / "spec.md").write_bytes(spec(lp=["| LP-01 | PR-99 を使う | L6 |"]).encode("utf-8"))
            self.assertEqual(gdd_check.main(args), 1)
            self.assertEqual(gdd_check.main(args[:4] + ["--spec", str(d / "nope.md")] + args[6:]), 2)
            gdd_check.CFG = gdd_check.load_config()


if __name__ == "__main__":
    unittest.main()
