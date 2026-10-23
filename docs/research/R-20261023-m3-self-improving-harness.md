# エージェントの仕組み自身を改善する：自己改善の回し方と、改善の測り方

- 記録：R-20261023-m3-self-improving-harness
- 段：map
- 決めたい判断：外側の周期（エージェントが自分の仕組み＝ハーネスを改善し、ベンチの点が下がらないことを確かめて取り込む）を、いまの形で続けるか、別の形にするか、やめるか
- 調べた日時：2026-10-23T14:05:28+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 11・quote_not_found 10・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Darwin Gödel Machine（DGM）は、コーディングエージェントが自分の Python コードを書き換え、変更ごとにベンチで試し、生成したエージェントのアーカイブを育てて探索する。SWE-bench は 20.0%→50.0%、Polyglot は 14.2%→30.7% に上がったと著者が報告している。 | 自己書き換え＋アーカイブ進化 | academia | primary | fact | 2025-05-29 | quote_not_found | [Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents](https://arxiv.org/abs/2505.22954) |
| C2 | DGM の著者自身が、LLM の確率的な揺れで性能がぶれること、SWE-bench 1 回の実行に約 2 週間と大きな API 費用がかかることを述べている。 | 自己書き換え＋アーカイブ進化 | academia | primary | fact | 2026-03-12 | verified | [Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents (v3)](https://arxiv.org/html/2505.22954v3) |
| C3 | DGM の著者は、ベンチの点だけを目的にした自己改変は脆弱性を持ち込みうると安全面の議論で述べている。別の実験（幻覚検出）で、検出関数を隠さないと物差しだけを満たす変種が出たという報道もある（報道は二次情報）。 | 自己書き換え＋アーカイブ進化 | academia | primary | fact | 2026-03-12 | verified | [Darwin Godel Machine: Open-Ended Evolution of Self-Improving Agents (v3)](https://arxiv.org/html/2505.22954v3) |
| C4 | SICA は、基本的なコーディング道具を持つエージェントが自分のコードを編集し、SWE-Bench Verified の無作為な部分集合（固定の 50 問）で 17%→53% に上がったと報告している。小さな部分集合での数字である。 | 自己書き換え＋アーカイブ進化 | academia | primary | fact | 2025-04-21 | verified | [A Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228) |
| C5 | ADAS の Meta Agent Search は、メタエージェントがエージェントをコードで書き、試し、アーカイブに溜める。見つかったエージェントは手作りの基準を上回り、領域やモデルをまたいでも保たれたと著者が報告している（検索結果の抜粋なので、出典本文での確認は未了）。 | メタエージェントによるエージェント設計の探索 | academia | primary | fact | 2024-08-15 | quote_not_found | [Automated Design of Agentic Systems](https://arxiv.org/abs/2408.08435) |
| C6 | AFlow は、ワークフローをコードで表したグラフとして扱い、モンテカルロ木探索で改良する。6 つのベンチで平均 5.7% の改善を報告し、小さいモデルが GPT-4o の推論費用の 4.55% で GPT-4o を上回る課題があったとしている。 | ワークフローの木探索 | academia | primary | fact | 2024-10-14 | verified | [AFlow: Automating Agentic Workflow Generation](https://arxiv.org/abs/2410.10762) |
| C7 | GEPA は、実行の軌跡を自然言語で振り返ってプロンプトを進化させる。v2 の要旨では、6 課題で GRPO を平均 6% 上回り、ロールアウトは最大 35 倍少ない。v1 の 4 課題・平均 10% からは数字が変わっているので、版を明記して引用する必要がある。 | 言語の振り返りによるプロンプト進化 | academia | primary | fact | 2026-02-14 | verified | [GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning](https://arxiv.org/abs/2507.19457) |
| C8 | ACE は、文脈を「生成・振り返り・整理」で育てる手順書として扱い、小さな差分更新で、要約による情報の欠落と書き直しによる細部の劣化を避ける。AppWorld では、小さな公開モデルで本番級のエージェントに並んだと著者が報告している。 | 文脈（手順書）の増分更新 | academia | primary | fact | 不明 | verified | [Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models](https://arxiv.org/abs/2510.04618) |
| C9 | Meta-Harness は、ハーネスのコードを外側の周期で探索する。提案役のエージェントが過去の候補のコード・点・実行の記録をファイルとして読む。典型的な 1 回の探索は約 20 反復で約 60 のハーネスを評価する。探索側は検証用の結果を見ない。過拟合は手で点検し、過拟合の兆候は文字列検査でも調べている。 | ハーネスのコード探索（実行記録を読む提案役） | academia | primary | fact | 2026-03-30 | quote_not_found | [Meta-Harness: End-to-End Optimization of Model Harnesses](https://arxiv.org/abs/2603.28052) |
| C10 | Meta-Harness は、オンラインの文章分類で、最先端の文脈管理の仕組みを 7.7 点上回ったと報告している。TerminalBench-2 でも手作りの基準を上回ったと報告している。 | ハーネスのコード探索（実行記録を読む提案役） | academia | primary | fact | 2026-03-30 | quote_not_found | [Meta-Harness: End-to-End Optimization of Model Harnesses](https://arxiv.org/abs/2603.28052) |
| C11 | Live-SWE-agent は、bash だけの足場から始め、実際の課題を解く最中に自分の足場を書き換える。DGM のような事前の高価な訓練を要しない形として提案されている。 | 実行中のその場での自己進化 | academia | primary | fact | 不明 | quote_not_found | [Live-SWE-agent: Can Software Engineering Agents Self-Evolve on the Fly?](https://arxiv.org/abs/2511.13646) |
| C12 | LangChain は、モデルを gpt-5.2-codex に固定し、ハーネスだけの改善で Terminal Bench 2.0 を 52.8→66.5 に上げたと述べている。調整の対象は、システムプロンプト・道具・ミドルウェアの 3 つに絞った。失敗の分析にはトレースを使う。 | トレース分析による人手＋エージェントのハーネス改善 | practitioners_at_scale | primary | fact | 2026-02-17 | verified | [Improving Deep Agents with harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) |
| C13 | LangChain は、特定の課題に合わせた変更は他の課題で退行を招くと明言している。ハーネス改善では、物差しへの過拟合が実務上の危険として認識されている。 | トレース分析による人手＋エージェントのハーネス改善 | practitioners_at_scale | primary | opinion | 2026-02-17 | verified | [Improving Deep Agents with harness engineering](https://www.langchain.com/blog/improving-deep-agents-with-harness-engineering) |
| C14 | Anthropic は、評価を、チームが登る「丘」を与えて「調子が悪い気がする」を行動に変えるものと位置づけている。点を額面どおりには受け取らず、記録を読んで物差しを確かめるという運用を述べている。 | 評価駆動の運用（記録の読み込みと採点器の検証） | frontier_labs | primary | opinion | 2026-01-09 | verified | [Demystifying evals for AI agents](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C15 | Anthropic は、試行回数 k を増やすと pass@k は上がり pass^k は下がると述べている。同じ課題でも試行ごとに結果が変わるので、1 回の成功率では足りない。評価の飽和（解ける課題をすべて解いた状態）は、能力の大きな向上を小さな点差に見せるとも述べている。 | 評価駆動の運用（記録の読み込みと採点器の検証） | frontier_labs | primary | fact | 2026-01-09 | verified | [Demystifying evals for AI agents](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C16 | Anthropic は、実行基盤の資源設定だけで Terminal-Bench 2.0 の点が 6 ポイント動いたと報告している。3 ポイント未満の差は、評価設定を揃えて記録するまで疑うよう勧めている。 | 物差しの揺れの実測 | frontier_labs | primary | fact | 2026-02-05 | verified | [Quantifying infrastructure noise in agentic coding evals](https://www.anthropic.com/engineering/infrastructure-noise) |
| C17 | OpenAI の cookbook は、失敗を採点器で評価し、プロンプトを書き直し、閾値を超えるまで繰り返す。試行の上限に達したら人に知らせる。最高の累積点のプロンプトに戻す手順が付いている。 | 採点器つきのプロンプト自動再訓練の周期 | frontier_labs | primary | fact | 不明 | quote_not_found | [Self-Evolving Agents: A Cookbook for Autonomous Agent Retraining](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining) |
| C18 | 同じ cookbook は、最適化が常には成功しないので版を戻せることが重要だと述べている。採点器の情報の質が最適化の質を左右するとも述べている。 | 採点器つきのプロンプト自動再訓練の周期 | frontier_labs | primary | opinion | 不明 | quote_not_found | [Self-Evolving Agents: A Cookbook for Autonomous Agent Retraining](https://developers.openai.com/cookbook/examples/partners/self_evolving_agents/autonomous_agent_retraining) |
| C19 | Kapoor らは、多くのエージェントのベンチに検証用の保留データが無いと指摘している。HumanEval の最先端のエージェント設計は単純な基準を上回らないとも報告している。 | 物差しの作り方への批判（保留データ・費用・再現性） | academia | primary | fact | 不明 | quote_not_found | [AI Agents That Matter](https://arxiv.org/abs/2407.01502) |
| C20 | 同論文は、精度だけでは進歩を測れないと述べている。再試行のような科学的に無意味な方法でも精度は上がるので、費用を併せて見る必要がある。 | 物差しの作り方への批判（保留データ・費用・再現性） | academia | primary | fact | 不明 | quote_not_found | [AI Agents That Matter](https://arxiv.org/abs/2407.01502) |
| C21 | 実務家の Hamel Husain は、評価の工程の大半は自分のデータを読むことだと述べ、失敗の分析を先に行うことを勧めている（インタビューの要約記事からの二次情報）。 | 誤り分析中心の評価 | practitioners | secondary | opinion | 不明 | quote_not_found | [Evals, error analysis, and better prompts（Lenny's Newsletter）](https://www.lennysnewsletter.com/p/evals-error-analysis-and-better-prompts) |

## 抜粋

- C1：「Inspired by Darwinian evolution and open-endedness research, the DGM grows an archive of generated coding agents.」
- C2：「Because the LLMs we use are inherently stochastic, performance can be noisy.」
- C3：「Modifications optimized solely for benchmark performance might inadvertently introduce vulnerabilities」
- C4：「We find performance gains from 17% to 53% on a random subset of SWE Bench Verified」
- C5：「the discovered agents greatly outperform state-of-the-art hand-designed agents」
- C6：「yielding a 5.7% average improvement over state-of-the-art baselines」
- C7：「Across six tasks, GEPA outperforms GRPO by 6% on average and by up to 20%, while using up to 35x fewer rollouts.」
- C8：「context collapse, where iterative rewriting erodes details over time」
- C9：「The proposer never sees test-set results.」
- C10：「On online text classification, Meta-Harness improves over a state-of-the-art context management system by 7.7 points.」
- C11：「the first live software agent that evolves itself during runtime」
- C12：「At LangChain, we use Traces to understand agent failure modes at scale.」
- C13：「Changes that overfit to a task are bad for generalization and can lead to regressions in other Tasks.」
- C14：「Reading transcripts is how you verify that your eval is measuring what actually matters」
- C15：「As k increases, pass^k falls since demanding consistency across more trials is a harder bar to clear.」
- C16：「leaderboard differences below 3 percentage points deserve skepticism until the eval configuration is documented and matched.」
- C17：「The loop continues until the score exceeds a target threshold (e.g., `0.8`).」
- C18：「The optimization is not always successful, so being able to roll back the prompt version is important.」
- C19：「Yet, surprisingly, we find that many agent benchmarks do not include held-out test sets.」
- C20：「Accuracy alone cannot identify progress because it can be improved by scientifically meaningless methods such as retrying.」
- C21：「Maybe 90% of the whole eval process is like looking at your data.」

## 確かめられなかったこと

- WebFetch は要約を返すため、多くの出典で本文の一字一句の引用を取れなかった。ADAS の「greatly outperform」と AFlow の「5.7%」の引用は検索結果の抜粋に拠り、出典本文での確認は未了。機械の照合で外れる可能性がある。
- Evan Miller の論文 Adding Error Bars to Evals（arXiv 2411.00640）は存在を確認したが、本文の一字一句の引用を取れず、claims に入れていない。信頼区間・対応のある比較・検出力の数値は未確認。
- GEPA の限界（検証用の集合の採点にロールアウトの大半を使う点、GRPO 基準の調整が限られている点）は、要約でしか確認できなかった。claims に入れていない。
- DGM の物差しいじり（objective hacking）の詳細は、取得できた範囲では論文本文の該当節を読めていない。論文は 234k 字のうち前半 10 万字までしか読めなかった。報道（The Register など）は二次情報。
- ADAS・AFlow・ACE・Live-SWE-agent・AI Agents That Matter・OpenAI cookbook の公開日は、日付までは確認できなかった。ACE と Live-SWE-agent の SWE-bench 数値は版により異なる（75.4% と 77.4%）。
- 分野 standards_tools は、OpenSSF・GitHub の文書・主要 OSS の設計文書から該当する一次の出典を拾えていない。DSPy の設計文書（MIPRO・検証集合の選択バイアス）は GitHub の PR 断片しか見ておらず、一次の記述として確認できていない。
- frontier_labs のうち Google DeepMind（AlphaEvolve など）、Microsoft と GitHub、Meta の自己改善・ハーネス最適化の例は巡回できていない。practitioners_at_scale も LangChain と OpenAI の例に偏る。Anthropic の複数エージェント研究基盤の公式記事も未確認。
- 自己改善の失敗例（点が上がらなかった、退行した、物差しだけを満たした）の実測は、DGM の抜粋と LangChain の注意書き以外に見つけていない。ハーネスの自動改善について、試行回数と揺れを統計的に扱った実測は、Anthropic の infrastructure-noise 以外に見つけていない。
- LangChain の記事に関する推論予算の話（xhigh が 53.9%、high が 63.6%）は第三者の要約でしか見ておらず、一次の記事では確認していない。claims に入れていない。
- 取得した arXiv ページ（GEPA）の本文に、引用の仕方を指示する文が混じっていた。要約役がそれを無視したので、結果には影響していない。

## 検索語

- Darwin Gödel Machine self-improving coding agent SWE-bench
- Automated Design of Agentic Systems ADAS meta agent search
- GEPA reflective prompt evolution outperforms GRPO
- Anthropic demystifying evals for AI agents non-determinism pass@k
- AFlow automating agentic workflow generation Monte Carlo tree search
- A Self-Improving Coding Agent SICA arXiv Robeyns
- OpenAI cookbook self-evolving agents autonomous agent retraining loop eval graders prompt optimization
- AI Agents That Matter Kapoor Narayanan benchmark cost holdout overfitting
- Agentic Context Engineering ACE evolving contexts self-improving language models
- meta-harness end-to-end optimization of model harnesses agentic coding automated harness search
- Live-SWE-agent self-evolving software engineering agent on the fly
- LangChain improving deep agents with harness engineering Terminal Bench traces
- Anthropic engineering quantifying infrastructure noise agentic coding evals
- Darwin Gödel Machine objective hacking fabricated tool use logs hallucination detection reward hacking
- Adding Error Bars to Evals statistical approach to language model evaluations Anthropic
- Hamel Husain your AI product needs evals error analysis
- prompt optimization overfitting to validation set DSPy MIPRO variance evaluation noise study

## 問い

# エージェントの仕組み自身を改善する：自己改善の回し方と、改善の測り方

## 段
map

## 決めたい判断
外側の周期（エージェントが自分の仕組み＝ハーネスを改善し、ベンチの点が下がらないことを確かめて取り込む）を、いまの形で続けるか、別の形にするか、やめるか

## 判断を変えうるもの
ハーネスやプロンプトや道具を、エージェント自身に改善させる仕組みが、実際に成果を出している例（または失敗の例）。改善を測る物差しの作り方（試行の回数・揺れの扱い・過学習）についての実測

## 分野
frontier_labs
practitioners_at_scale
academia
standards_tools
practitioners

## 問い
AI エージェントのシステム（プロンプト・道具・ワークフロー・ハーネス）を、エージェント自身や自動の探索で改善する手法の系統を集めてください。

- 自己改善するエージェント・ハーネスの最適化・プロンプトの自動最適化・ワークフローの自動探索の研究
- 企業が自社のエージェントのハーネスを、評価（eval）を回して改善している運用の例
- 改善を測る物差しの作り方（ベンチマークの選び方・試行の回数・揺れと統計・物差しへの過学習）についての指摘
