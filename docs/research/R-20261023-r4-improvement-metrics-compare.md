# エージェントの開発環境の改善を測る物差しの比較（比較）

- 記録：R-20261023-r4-improvement-metrics-compare
- 段：compare
- 決めたい判断：1 人の操縦士が、AI のエージェントに台帳のタスクを実装させ、CI の試験で合否を決めて PR をマージする開発環境（週に数十件）で、環境（ハーネス：指示・道具・門・手順）の改善を何で測り、何をもって「改善した」と判定するか
- 調べた日時：2026-10-23T16:33:50+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 8・quote_not_found 3・unreachable 1・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic は、エージェントは非決定的なので各課題を複数回（trial）走らせることを前提にしている。pass@k は k 回のうち 1 回でも成功する確率、pass^k は k 回すべて成功する確率と定義している。 | 作り直したベンチ（複数回走行・pass@k/pass^k） | frontier_labs | primary | fact | 2026-01-09 | verified | [Demystifying evals for AI agents (Anthropic Engineering)](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C2 | Anthropic は、実際の失敗から取った 20〜50 件の単純な課題で評価を始めてよいと述べている。ただし根拠は、変更の効果が大きいときは少ない標本で足りる、という点である。小さな改善には当てはまらない。 | 作り直したベンチ（小さく保つ退行検出） | frontier_labs | primary | opinion | 2026-01-09 | verified | [Demystifying evals for AI agents (Anthropic Engineering)](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C3 | Anthropic は、評価が飽和すると（解ける課題をすべて通すと）退行の検出はできるが、改善の信号は出なくなると述べている。これは候補 3 の「小さく保って退行だけに使う」前提と同じ見方である。 | 作り直したベンチ（飽和） | frontier_labs | primary | fact | 2026-01-09 | verified | [Demystifying evals for AI agents (Anthropic Engineering)](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C4 | Anthropic は、採点器を迂回や悪用に強く作ること、通すには本当に問題を解く必要がある設計にすることを求めている。同じ記事は、事前の自動評価は実際の使われ方と合わないと偽の安心を生むとも書いている。本番の監視が、分布のずれと想定外の失敗を拾う役を担う。 | 実運用の記録の指標＋ベンチの併用 | frontier_labs | primary | opinion | 2026-01-09 | verified | [Demystifying evals for AI agents (Anthropic Engineering)](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C5 | SWE-bench Verified を単独で 1 回走らせた pass@1 は、どの回を選ぶかで 2.2〜6.0 ポイント揺れる。2〜3 ポイントの改善は評価の揺れかもしれない。1 回走行の点の比較は、小さな改善を検出できない根拠になる。 | 作り直したベンチ（揺れ・統計） | academia | primary | fact | 2026-02-06 | verified | [On Randomness in Agentic Evals](https://arxiv.org/abs/2602.07150v1) |
| C6 | 同じ論文は、報告された 2〜3 ポイントの改善が、本物の進歩ではなく評価の揺れを映している可能性があると述べている。 | 作り直したベンチ（揺れ・統計） | academia | primary | fact | 2026-02-06 | verified | [On Randomness in Agentic Evals](https://arxiv.org/abs/2602.07150v1) |
| C7 | Anthropic の Evan Miller の論文は、評価を実験として扱い、2 モデルの比較（対にして分散を減らす）や必要な標本数の見積もり（検出力分析）の式を示している。同じ課題を前後で走らせる対比較は、課題の出来の違いによる揺れを減らせる。数値は第三者の道具の例（500 問・基準 50% で最小検出差が約 8.9 点）しか取れていない。 | 統計の扱い（対比較・検出力） | frontier_labs | primary | fact | 2024-11-01 | verified | [Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations](https://arxiv.org/abs/2411.00640) |
| C8 | METR は、SWE-bench Verified のテストを通ったパッチ 296 件を実在のメンテナ 4 人に審査させた。およそ半分は main にマージされないと判定された。自動採点の合格率はメンテナのマージ判断より平均で約 24.2 ポイント高かった（人が書いた正解パッチでも再承認は約 68%）。テスト合格だけの物差しは、人の判断とずれうる。 | 実運用の記録の指標（人のマージ判断） | practitioners_at_scale | primary | fact | 2026-03-10 | quote_not_found | [Many SWE-bench-Passing PRs Would Not Be Merged into Main](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/) |
| C9 | METR は、この結果が能力の限界を示すものではないと注意している。研究は 1 つのベンチの一部と 1 種のハーネスに限られ、レビューは CI なしで静的に行われ、フィードバック後の修正も見ていない。マージ判断を物差しにする場合も、レビューする人による揺れが大きい。正解パッチの再承認が約 68% にとどまることがその根拠である。 | 実運用の記録の指標（人のマージ判断） | practitioners_at_scale | primary | fact | 2026-03-10 | quote_not_found | [Many SWE-bench-Passing PRs Would Not Be Merged into Main](https://metr.org/notes/2026-03-10-many-swe-bench-passing-prs-would-not-be-merged-into-main/) |
| C10 | OpenAI は 2026-02-23 の投稿で、最前線のコーディング評価に SWE-bench Verified を使うのをやめ、SWE-bench Pro を勧めた。理由は汚染と、正しい解を落とす欠陥のあるテストである。公開ベンチが飽和し、すり抜けと汚染を受けた例になる。OpenAI のページは取得時に 403 で、引用は検索結果の抜粋による。 | 作り直したベンチ（飽和・汚染） | frontier_labs | primary | fact | 2026-02-23 | unreachable | [Why we no longer evaluate SWE-bench Verified](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/) |
| C11 | τ-bench は pass^k を、k 回の試行がすべて成功する確率として導入した。最前線の関数呼び出しエージェントでも成功率は 50% 未満で、一貫性は低い（gpt-4o は retail で pass^8 が 25% 未満）。pass^k は一貫性を測れるが、k を増やすほど走行回数が増えて費用が膨らむ。 | 作り直したベンチ（pass^k） | academia | primary | fact | 2024-06-18 | quote_not_found | [τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains](https://arxiv.org/pdf/2406.12045) |
| C12 | BenchJack の監査は、SWE-bench の評価器が、エージェントのパッチが書き換えられるテスト用コンテナ内のテスト出力を信用していることを設計の欠陥として挙げている。ベンチの点は、指標だけを満たす変更にすり抜けられうる。本文の一字一句の写しは取れなかったので、quote は要約を避けて検索結果の記述をそのまま使う。 | すり抜け（ベンチの悪用） | academia | primary | fact | 2026-05-14 | verified | [Do Androids Dream of Breaking the Game? Systematically Auditing AI Agent Benchmarks with BenchJack](https://arxiv.org/pdf/2605.12673) |

## 抜粋

- C1：「pass^k measures the probability that all k trials succeed.」
- C2：「In reality, 20-50 simple tasks drawn from real failures is a great start.」
- C3：「An eval at 100% tracks regressions but provides no signal for improvement.」
- C4：「Make your graders resistant to bypasses or hacks.」
- C5：「single-run pass@1 estimates vary by 2.2 to 6.0 percentage points depending on which run is selected」
- C6：「reported improvements of 2--3 percentage points may reflect evaluation noise rather than genuine algorithmic progress」
- C7：「Evaluations are critical for understanding the capabilities of large language models (LLMs).」
- C8：「roughly half of test-passing SWE-bench Verified PRs would not be merged into main by repo maintainers.」
- C9：「We found that about 68% of golden patches are indeed merged by our maintainers.」
- C10：「SWE-bench Verified is increasingly contaminated. We recommend SWE-bench Pro.」
- C11：「even state-of-the-art function calling agents (like gpt-4o) succeed on < 50% of the tasks, and are quite inconsistent (pass^8 < 25% in retail)」
- C12：「Do Androids Dream of Breaking the Game? Systematically Auditing AI Agent Benchmarks with BenchJack」

## 確かめられなかったこと

- 候補 1 の指標（人の手直し無しのマージ率、1 回目の門通過率、マージ後 N 日以内の revert 率）について、週に数十件で前後比較したときの必要件数・揺れを扱った一次の出典は見つからなかった。比率の標準誤差などの一般統計は使えるが、出典は無い
- 最前線の企業が運用中に「手直し無しのマージ率」や「revert 率」を改善の主指標にしている、という一次の出典は取れなかった。Stripe の Minions（週 1,300 件超のマージ）、Ramp、Vellum の数字は、まとめ記事か当事者の自己申告で、一次の公式文書では確認できていない
- DORA 2025 は、変更失敗率と rework 率を不安定さの指標として扱っている。ただし取れたのは解説記事（secondary）だけで、DORA の本文と数字は確認できていない
- 候補 2・3 の費用（複数回走行のトークン・時間）を実測した一次の出典は取れなかった。「費用は先に見え、効果は後から積み上がる」という Anthropic の記述はあるが、金額の数字は無い。検索結果にあった MLflow の記事（3 シード以上、コストを指標に含める）は、ブログ形式の実務ガイドである
- DeepSWE の論文（run 数が少ないと区間が狭く見える）と Wilson 区間（n=100 で半幅 7〜9.5 点）は、検索結果の要約でしか確認できず、本文の抜粋は取れなかったので claims に入れていない
- BenchJack、Reward Hacking Benchmark、SWE-Bench Pro Verified（leak で順位が 21.48 点下がったという報告）、reward-hackability 28.5% の数字は、本文の抜粋が取れず、HackerNoon やブログ経由の情報しかない。これらは claims に入れていない。BenchJack の 1 件は quote が題名だけで、confidence を low にした
- OpenAI のページは取得時に 403 で、quote は検索結果の抜粋に頼った。本文との照合はできていない
- 候補 3 そのもの（実運用の指標を主、小さなベンチを退行検出）を採っている企業の一次の報告は見つからなかった。Anthropic の記事は、離線の評価と本番の監視を併用する構成を述べているにとどまる
- pass@k と pass^k の小標本での検出力を直接比べた一次の出典は見つからなかった。k を増やすと pass@k は 100% に、pass^k は 0% に近づく、という説明は第三者の要約で、一次では確認していない

## 検索語

- Demystifying evals for AI agents pass@k pass^k Anthropic
- SWE-bench Verified contaminated flawed tests OpenAI no longer evaluate
- METR study AI coding agents merged PRs maintainers would not merge passing tests SWE-bench
- tau-bench pass^k reliability agents consistency
- Adding Error Bars to Evals: A Statistical Approach to Language Model Evaluations
- coding agent PR merge rate production metric engineering blog Stripe minions OR Cursor OR Spotify background coding agent merged PRs
- agent benchmark reward hacking agent exploits grader SWE-bench git log OR "test overfitting" OR "agents cheat" paper
- benchmark run-to-run variance agents SWE-bench single run noise confidence interval number of runs
- DORA 2025 report AI-assisted development change failure rate rework rate instability

## 問い

# エージェントの開発環境の改善を測る物差しの比較（比較）

## 段
compare

## 決めたい判断
1 人の操縦士が、AI のエージェントに台帳のタスクを実装させ、CI の試験で合否を決めて PR をマージする開発環境（週に数十件）で、環境（ハーネス：指示・道具・門・手順）の改善を何で測り、何をもって「改善した」と判定するか

## 判断を変えうるもの
候補のどれかが、小さな標本（週に数十件）で改善を検出できない・誤った方向に引っ張る・すり抜けられる、という一次の出典や実測があれば、その候補を外すか条件を足す

## 候補
1. 実運用の記録の指標：タスクごとの「人の手直し無しでマージされた率」「1 回目の試行で門を通った率」「マージ 1 件あたりのトークンと費用」「マージ後 N 日以内の revert と修正の率」を、前後の期間で比べる
2. 作り直したベンチ：飽和しない難しい課題を増やし、各課題を複数回走らせて、点（pass@k と pass^k）を比べる
3. 1 を主の物差しにし、2 は小さく保って退行の検出だけに使う（変更の前後で同じ課題を走らせる）

## 問い
上の 3 つの候補を、次の観点で比べてください。各観点について、一次の出典（提供元の文書・論文・大規模な実務家の報告）を挙げてください。

- 小さな標本で改善を検出できるか（必要な件数・揺れの大きさ・統計の扱い）
- すり抜け（指標だけを満たす変更）への強さ
- 費用（トークン・時間）
- 最前線の企業や大規模な実務家が、実際にどちらを使っているか
