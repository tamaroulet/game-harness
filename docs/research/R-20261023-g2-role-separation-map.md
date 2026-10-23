# AI のエージェントの開発で、判断・実行・検証の役を分けることの効果と形（俯瞰）

- 記録：R-20261023-g2-role-separation-map
- 段：map
- 決めたい判断：1 人の人間が AI のエージェントに開発を任せる環境で、計画と判断を下す役（総監督）・実行する役（実行役と実装役）・検める役（検証と審査）を、別のエージェント（別の文脈・別系統のモデル）に分けるか、どう分けるか。今は 1 つのエージェント（実行役）が、ADR を書き、調べものの問いと候補を立て、同じ系統のモデルに調べさせ、自分の ADR を確かめる試験を書き、自分で完了にしている
- 調べた日時：2026-10-23T16:47:07+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 16・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | 自分の出力を自分で評価させると、評価役の LLM が自分の出力を他より高く採点する自己選好バイアスが出る。人間の注釈者は同等と見ているのに差がつく。 | 自己選好バイアスの実測 | academia | primary | fact | 2024-04-15 | verified | [LLM Evaluators Recognize and Favor Their Own Generations (NeurIPS 2024)](https://arxiv.org/abs/2404.13076) |
| C2 | GPT-4 と Llama 2 は、そのままでも自分の出力を他の LLM や人間の文章から見分けられる。微調整で自己認識を強めると、自己選好の強さが直線的に増える。因果の説明は単純な交絡では崩れないと、著者は述べている。 | 自己選好バイアスの実測 | academia | primary | fact | 2024-04-15 | verified | [LLM Evaluators Recognize and Favor Their Own Generations (NeurIPS 2024)](https://arxiv.org/abs/2404.13076) |
| C3 | LLM を審査役にする方法そのものが、位置・冗長さ・自己高評価の偏りを持つ。それでも人間との一致率は 80% を超える。 | 自己選好バイアスの実測 | academia | primary | fact | 2023-06-09 | verified | [Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685) |
| C4 | 1 つの大きな審査役より、別系統のモデルを集めた小さな審査団（PoLL）のほうが、同じモデル内の偏りが小さく、費用は 7 倍以上安いと報告されている。 | 別系統モデルによる審査団 | academia | primary | fact | 2024-04-29 | verified | [Replacing Judges with Juries: Evaluating LLM Generations with a Panel of Diverse Models](https://arxiv.org/abs/2404.18796) |
| C5 | 外からの帰還（外部の助言）が無いと、LLM は自分の答えを直せず、自己修正のあとでかえって性能が下がることがある。 | 自己修正の限界 | academia | primary | fact | 2023-10-03 | verified | [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798) |
| C6 | Anthropic の調査機能は、主導役が計画して並列の副役に仕事を渡す orchestrator-worker 構成で、社内の調査評価では単独の Opus 4 を 90.2% 上回った。 | orchestrator-worker | frontier_labs | primary | vendor_claim | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C7 | 同じ記事は、性能の分散の 80% をトークン使用量だけで説明でき、マルチエージェントはチャットの約 15 倍のトークンを使うと述べている。つまり効果の一部は費やした計算量による。 | orchestrator-worker | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C8 | Anthropic は、コーディングは調査より並列にできる部分が少ないので、この構成の向きが弱いと述べている。 | orchestrator-worker | frontier_labs | primary | opinion | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C9 | 副役は作業の成果を外部の置き場に保存し、軽い参照だけを主導役に返す。主導役に渡す情報を絞る仕組みの一例。 | 判断役への情報の絞り込み | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C10 | 副役は空の文脈で集中した仕事を引き受け、広く探索しても、主導役には要約だけを返す。 | 判断役への情報の絞り込み | frontier_labs | primary | fact | 2025-09-29 | verified | [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) |
| C11 | Anthropic の指針は、評価役と最適化役を分けるループ（evaluator-optimizer）を、構成の型の 1 つとして挙げている。同じ指針は、できるだけ単純な解から始めるよう勧めている。 | evaluator-optimizer（生成と評価の分離） | frontier_labs | primary | opinion | 2024-12-19 | verified | [Building effective agents](https://www.anthropic.com/research/building-effective-agents) |
| C12 | Cognition は、副役に仕事を分けるマルチエージェントは壊れやすいと主張している。理由は、行動が暗黙の決定を含み、文脈が共有されないと決定が食い違うからだという。 | 単一スレッド反対論 | practitioners_at_scale | primary | opinion | 2025-06-12 | verified | [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) |
| C13 | マルチエージェントの失敗 1600 件以上の分析で、失敗はモデルの力不足だけでなく、組織設計と調整の問題から生じるとされた。検証の失敗（不完全な検証 8.20%、誤った検証 9.10%、早すぎる終了 6.20%）も、分類の独立した 1 群をなす。 | マルチエージェントの失敗分類 | academia | primary | fact | 2025-03-17 | verified | [Why Do Multi-Agent LLM Systems Fail? (MAST)](https://arxiv.org/html/2503.13657v3) |
| C14 | MAST を使った ChatDev への介入で、成功率が 9.4% 上がった。同じモデルのまま役割と調整の設計を直すだけで効果が出る例。 | マルチエージェントの失敗分類 | academia | primary | fact | 2025-03-17 | verified | [Why Do Multi-Agent LLM Systems Fail? (MAST)](https://arxiv.org/html/2503.13657v3) |
| C15 | エージェントの構成を多数のタスクで測った研究。単独の基準が一定以上なら、調整の利得は減る。中央の検証が無い構成は誤りを広げやすい。効果は分解しやすい金融推論で +80.8%、逐次の計画で -70.0% と、タスクの形で正負が逆になる。 | 構成の効果のタスク依存 | academia | primary | fact | 2025-12-09 | verified | [Towards a Science of Scaling Agent Systems](https://arxiv.org/abs/2512.08296) |
| C16 | GitHub は Copilot コーディングエージェントの PR で、依頼した人の承認が必須承認数に数えられず、別の審査者の承認が要る規則にしている。作る役と承認する役を機械で分ける実例。 | 職務分離の機械的な強制（maker-checker） | standards_tools | primary | fact | 不明 | verified | [Review Copilot's pull requests (GitHub Docs)](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/review-copilot-prs) |

## 抜粋

- C1：「One such bias is self-preference, where an LLM evaluator scores its own outputs higher than others'」
- C2：「By fine-tuning LLMs, we discover a linear correlation between self-recognition capability and the strength」
- C3：「including position, verbosity, and self-enhancement biases」
- C4：「exhibits less intra-model bias due to its composition of disjoint model families, and does so while being over seven times less expensive.」
- C5：「LLMs struggle to self-correct their responses without external feedback」
- C6：「Our Research system uses a multi-agent architecture with an orchestrator-worker pattern」
- C7：「We found that token usage by itself explains 80% of the variance」
- C8：「most coding tasks involve fewer truly parallelizable tasks than research」
- C9：「Subagents call tools to store their work in external systems, then pass lightweight references back to the coordinator.」
- C10：「specialized sub-agents can handle focused tasks with clean context windows.」
- C11：「one LLM call generates a response while another provides evaluation and feedback in a loop.」
- C12：「Actions carry implicit decisions, and conflicting decisions carry bad results」
- C13：「no or incomplete verification (FM-3.2, 8.20%)」
- C14：「MAS failure is not merely a function of challenges in the underlying model」
- C15：「architectures without centralized verification tend to propagate errors more than those with centralized coordination.」
- C16：「Another reviewer must approve the pull request before it can be merged.」

## 確かめられなかったこと

- practitioners：名の通った実務家（個人）の、職務分離や役の分け方についての当人の記事・講演は、今回取れなかった（検索していない）。Cognition は practitioners_at_scale に入れた
- standards_tools：NIST SP 800-53 AC-5（職務の分離）の本文と、SLSA の二人レビューの要件は、ページを取得できず（AC-5 は目次のみ、SLSA v1.0 には該当の語なし）、引用できなかった。OpenSSF の AI エージェント向け指針も未確認。GitHub の文書は、公開日が不明で、n+1 承認の記述は当該ページに無かった
- frontier_labs：OpenAI の CriticGPT の記事は 403 で取得できず、批評役が人間より好まれた割合などは未確認。Google DeepMind・Microsoft・Meta の一次の文書は、個別には見ていない（DeepMind の可能性がある 2512.08296 も所属が確認できず、frontier_labs の出典としては扱っていない）
- 職務分離を AI エージェントの運用に当てた指針（maker-checker、four-eyes という語で書かれたもの）の一次の出典は、GitHub の承認規則以外に見つからなかった
- 別系統のモデルや別の文脈で評価する効果のうち、コード生成の評価で自己選好を測った実測は未確認。取れたのは要約と一般の判定の研究。Panickssery 論文の、ラベル反転で好みの向きが変わるという二次の記述は本文で確かめていない
- 多くの quote は WebFetch が返した抜粋で、途中を省いた断片。本文との一字一句の照合は後段の機械に任せる。Anthropic 記事の 90.2% は社内評価の売り手の主張
- MAST の ChatDev +9.4% の値は、quote の文とは別の箇所の記述で、quote 自体にはない

## 検索語

- LLM evaluators recognize and favor their own generations self-preference bias
- Anthropic How we built our multi-agent research system orchestrator-worker
- Why do multi-agent LLM systems fail? MAST taxonomy
- separation of duties AI coding agent cannot approve its own pull request required reviewers GitHub Copilot coding agent
- Anthropic building effective agents evaluator-optimizer orchestrator-workers workflow

## 問い

# AI のエージェントの開発で、判断・実行・検証の役を分けることの効果と形（俯瞰）

## 段
map

## 決めたい判断
1 人の人間が AI のエージェントに開発を任せる環境で、計画と判断を下す役（総監督）・実行する役（実行役と実装役）・検める役（検証と審査）を、別のエージェント（別の文脈・別系統のモデル）に分けるか、どう分けるか。今は 1 つのエージェント（実行役）が、ADR を書き、調べものの問いと候補を立て、同じ系統のモデルに調べさせ、自分の ADR を確かめる試験を書き、自分で完了にしている

## 判断を変えうるもの
自分の出力を自分で評価する LLM の偏り（自己選好・確証バイアス）の実測や、計画役・実行役・検証役を分けたマルチエージェントの構成が品質を上げる・上げない証拠、職務の分離（separation of duties、maker-checker）を AI のエージェントに当てた実践があれば、それで役の分け方を決める

## 分野
frontier_labs practitioners_at_scale academia standards_tools practitioners

## 問い
次を、分野ごとに一次の出典で集めてください。

- LLM が自分（または同じ系統のモデル）の出力を評価するときの偏り（self-preference bias、self-enhancement）の実測と、別系統のモデルや別の文脈で評価する効果
- 計画・実行・検証を別のエージェントに分けた構成（orchestrator-worker、planner-executor-verifier、critic）の効果と、分けすぎたときの失敗（調整の費用、責任のあいまいさ）
- 職務の分離（separation of duties、maker-checker、four-eyes principle）や利害の相反の扱いを、AI のエージェントの運用に当てた実践や指針
- 判断役が実行の細部に引きずられない仕組み（抽象度の分離、判断役に渡す情報の絞り方）
