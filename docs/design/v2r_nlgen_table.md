# v2r 描画器の対応表（U4 の査読用）

`harness/nlgen.py` の語句の対応表（`table()`）の全文と、その sha256。docs/design/v2r_protocol.md §3・§13 の U4 のとおり、人間が一度だけ査読し、本 PR のマージで凍結する。

- 対応表 sha256：`959aedeb8eec960a0ec56b813c77d1af0d55709ceef30c512a27088836f60047`
- 計算：`json.dumps(table(), ensure_ascii=False, sort_keys=True)` の UTF-8 の sha256（`nlgen.table_sha256()`）
- 確かめ方：`python -m harness.nlgen experiments/v2r/properties.json` の末尾の「対応表 sha256」と、語彙と描画の検査（「合格」）
- 凍結の仕組み：`tests/test_nlgen.py` が sha256 をこの値に固定する。対応表を変えるとテストが落ちるので、変えるときは本書と固定値を同じ PR で改め、改めて人間が査読する

## 査読の観点

1. 語彙の対応は全単射か（識別子・関数・演算子が 1 対 1、語句どうしが重ならない。`check_vocabulary` が機械的に確かめる）
2. 各語句が、形式の仕様（B−G・B）と同じ事実を、足さず引かずに言っているか（機械では確かめられない。査読の本体）
3. 関数の意味の説明（定義の閉包）が、性質テストの意味と食い違っていないか

## 対応表の全文

```json
{
  "ARITH": {
    "+": "{l}に{r}を足した値",
    "-": "{l}から{r}を引いた値"
  },
  "BOOL_CLAUSE": {
    "False": "{v}が偽である",
    "True": "{v}が真である"
  },
  "COMPARE": {
    "!=": "{l}が{r}と等しくない",
    "<": "{l}が{r}より小さい",
    "<=": "{l}が{r}以下である",
    "==": "{l}が{r}と等しい",
    ">": "{l}が{r}より大きい",
    ">=": "{l}が{r}以上である"
  },
  "CONST_CLAUSE": {
    "false": "決して成り立たない",
    "true": "常に成り立つ"
  },
  "ENUMS": {
    "GamePhase": {
      "GameOver": "ゲームオーバー",
      "Playing": "プレイ中",
      "Ready": "開始待機"
    },
    "MinoType": {
      "I": "I ミノ",
      "J": "J ミノ",
      "L": "L ミノ",
      "O": "O ミノ",
      "S": "S ミノ",
      "T": "T ミノ",
      "Z": "Z ミノ"
    },
    "Rotation": {
      "Left": "左向き",
      "Right": "右向き",
      "Spawn": "出現向き",
      "Two": "逆向き"
    }
  },
  "FEEDBACK_NOTE": "  （この性質：{s}）",
  "FUNC_DOCS": {
    "drop": "「m を下へ落としきったもの」：前の盤面で、置ける限り m を下へ 1 マスずつ動かしきったミノ",
    "fits": "「m が置ける」：前の盤面で、m の 4 マスがすべて盤面の内側（幅 PR-06、高さ PR-08）にあり、固定ブロックに重ならないこと",
    "in_board": "「m が盤面の内側にある」：m の 4 マスがすべて盤面の内側にあること",
    "kick": "「m を dir 段回して補正候補を試した結果」：m を dir 段回したものに、GddReference.Kicks(m の形, m の向き, 回した後の向き) の候補 (dx, dy) を表の順に足し、前の盤面で最初に置けるもの。どれも置けなければ無し",
    "moved": "「m を x に dx、y に dy 動かしたもの」：m の X 座標に dx、Y 座標に dy を足したミノ（形と向きは同じ）",
    "occupied_after": "「後の盤面の (x, y) が埋まっている」：後の盤面で (x, y) が固定ブロックであること。盤面の外は埋まっているとみなす",
    "occupied_before": "「前の盤面の (x, y) が埋まっている」：前の盤面で (x, y) が固定ブロックであること。盤面の外は埋まっているとみなす",
    "rotated": "「m を dir 段回したもの」：m の向きを 1 段回したミノ（位置は同じ）。dir が 1 なら 出現向き → 右向き → 逆向き → 左向き → 出現向き の順に 1 つ進め（時計回り）、-1 なら 1 つ戻す"
  },
  "GROUP": "（{c}）",
  "INPUT": {
    "HardDrop": "ハードドロップ",
    "Left": "左",
    "Right": "右",
    "RotateCcw": "左回転",
    "RotateCw": "右回転",
    "SoftDrop": "ソフトドロップ"
  },
  "INPUT_CLAUSE": {
    "False": "{m}の入力が無い",
    "True": "{m}の入力がある"
  },
  "LITERALS": {
    "false": "偽",
    "null": "無し",
    "true": "真"
  },
  "LOGIC": {
    "&&": "、かつ",
    "||": "、または"
  },
  "MINO": {
    "Rotation": "向き",
    "Type": "形",
    "X": "X 座標",
    "Y": "Y 座標"
  },
  "NEG": "マイナス{v}",
  "NOT": "（{c}）ではない",
  "NULL_COMPARE": {
    "!=": "{v}がある",
    "==": "{v}が無い"
  },
  "PRED_FUNCS": {
    "fits": [
      "{0}が置ける",
      "{0}が置けない"
    ],
    "in_board": [
      "{0}が盤面の内側にある",
      "{0}が盤面の内側に無い"
    ],
    "occupied_after": [
      "後の盤面の ({0}, {1}) が埋まっている",
      "後の盤面の ({0}, {1}) が埋まっていない"
    ],
    "occupied_before": [
      "前の盤面の ({0}, {1}) が埋まっている",
      "前の盤面の ({0}, {1}) が埋まっていない"
    ]
  },
  "READING": "- 「前の」「後の」は、その Tick の前と後の状態。「入力」は、その Tick の入力\n- ミノの 4 マス：GddReference.Shape(形, 向き) の各 (x, y) について (X 座標 + x, Y 座標 + y)。Y 座標が 1 減る向きが下",
  "ROOTS": {
    "after": "後の",
    "before": "前の"
  },
  "SENTENCE": "- {id}（{rule}）：{given}とき、{then}",
  "STATE": {
    "ActiveMino": "操作中のミノ",
    "BlockCount": "ブロック数",
    "LinesCleared": "消したライン数",
    "LockedMinoCount": "固定したミノの数",
    "Phase": "局面",
    "Score": "得点",
    "TickCount": "経過 Tick 数"
  },
  "VALUE_FUNCS": {
    "drop": "{0}を下へ落としきったもの",
    "kick": "{0}を {1} 段回して補正候補を試した結果",
    "moved": "{0}を x に {1}、y に {2} 動かしたもの",
    "rotated": "{0}を {1} 段回したもの"
  }
}
```
