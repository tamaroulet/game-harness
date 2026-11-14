# 完了の取り決めと、実装の後の評価役（Anthropic の長く走るアプリのハーネス）

- 記録：R-20261114-harness-contract-evaluator
- 段：compare
- 決めたい判断：E-1 の第 2 関門で、検証役が何を受け取り、何を出し、どう走るか。実装の前に作るのは完了の取り決めか、テストのコードか
- 調べた日時：2026-11-14T20:36:52+09:00
- 調べ役：seat（）
- 照合：verified 8・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | フルスタックのハーネスでは、各 sprint の前に、生成役と評価役が sprint contract を取り決めた。その一まとまりの仕事で何をもって完了とするかを、コードを書く前に合意する。 | 完了の取り決めを先に置く | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C2 | 取り決めには、完了を確かめるために試す振る舞い（testable behaviors）を書いた。生成役が作るものと確かめ方を出し、評価役がそれを見て、合意するまで繰り返した。 | 完了の取り決めを先に置く | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C3 | 実装の後、評価役は Playwright の MCP で、利用者と同じように動いているアプリを操作し、UI の機能・API の口・データベースの状態を試した。テストのコードを先に書く形ではない。 | 実装の後に動くものを外から確かめる | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C4 | 箱から出したままの Claude は QA の役として弱い。正しい問題を見つけても、大したことではないと自分を説き伏せて、仕事を通してしまった。 | 評価役の甘さと較正 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C5 | 生成役に自分の仕事を厳しく見させるより、独立した評価役を疑い深く調整するほうが、ずっと扱いやすい。 | 評価役の甘さと較正 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C6 | 較正は、評価役の記録を読み、人の判断と分かれた例を見つけて頼みを直す周回で行い、妥当に採点するまで何周もかかった。 | 評価役の甘さと較正 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C7 | 較正の後も、評価役が十分に試さなかった深い所の機能には、見つからない不具合が残った。 | 評価役の甘さと較正 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |
| C8 | sprint の形を外した版では、評価役を走りの最後の 1 回にした（評価役を仕事ごとに入れるかは、モデルの力で変わるつまみ。費用に見合う条件は R-20261113-v4-harness-principles#C8）。 | 評価役を入れる頻度 | frontier_labs | primary | fact | 2026-03-24 | verified | [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps) |

## 抜粋

- C1：「the generator and evaluator negotiated a sprint contract」
- C2：「the testable behaviors that would be tested to verify completion」
- C3：「the evaluator used the Playwright MCP to click through the running application the way a user would」
- C4：「Out of the box, Claude is a poor QA agent」
- C5：「tuning a standalone evaluator to be skeptical turns out to be far more tractable than making a generator critical of its own work」
- C6：「It took several rounds of this development loop before the evaluator was grading in a way that I found reasonable」
- C7：「undiscovered bugs in more deeply nested features」
- C8：「I moved the evaluator to a single pass at the end of the run」

## 確かめられなかったこと

- 会話の席が 2026-11-14 に原文を読み、抜粋を機械で照合した。記事は 1 社の 1 つの開発の記録で、数の比べ（評価役あり・なし）は無い
- 記事には、実装の前にテストのコードを書く（TDD）形の記述は無かった（無いことは照合できない）
- sprint contract の段を、sprint の形を外した後の版でも残したかは、記事に書かれていない
- 評価役の較正は頼みの文面の調整で、憲章 §5 では効きに数えないもの。手元では、較正でなく、確かめを機械で走るものに限ることで手当てする（設計 §4-2）

## 検索語

- （会話の席が原文を直接読んだ。検索は使っていない）https://www.anthropic.com/engineering/harness-design-long-running-apps

## 問い

# 第 2 関門（実装の後の、文脈を共有しない検証）の形

## 段
compare

## 決めたい判断
E-1 の第 2 関門で、検証役が何を受け取り、何を出し、どう走るか。実装の前に作るのは完了の取り決めか、テストのコードか

## 判断を変えうるもの
最前線のハーネスが、実装の前にテストのコードを作って関門にしている一次の記述。評価役が較正なしで十分に厳しい記述

## 候補
- 実装の前：完了の取り決め（確かめられる振る舞い）／テストのコード
- 実装の後：動くものを外から操作して確かめる評価役／テストのコードを書く検証役

## 問い
Anthropic の長く走るアプリのハーネスは、完了をいつ・どの形で決め、実装の後に何で確かめ、評価役の甘さにどう手当てしたか。
