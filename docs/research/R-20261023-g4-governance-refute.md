# 決定の権限と役の分け方の案への反証（反証）

- 記録：R-20261023-g4-governance-refute
- 段：refute
- 決めたい判断：1 人の人間（操縦士）が AI のエージェントに開発を任せる環境で、次の形を暫定の枠組みとして採るか
- 調べた日時：2026-10-23T16:51:16+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 4・quote_not_found 1・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic reports that its parallel multi-agent research system cut research time by up to 90% on complex queries, which is a cost of keeping work sequential in one executor (candidate 1). | 並列サブエージェント（読み取り中心） | frontier_labs | primary | vendor_claim | 2025-06-13 | quote_not_found | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C2 | Anthropic reports that multi-agent systems use about 15 times more tokens than chats, which means extra review and coordination agents add real cost (relevant to candidates 2 and 4). | マルチエージェント（読み取り中心） | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C3 | In a study of nine frontier LLMs from seven model families on three NLI datasets, the panel of judges effectively gave only about two independent votes' worth of information, because shared mistakes removed most of the independence (against candidate 2's assumption that a separate-family reviewer adds an independent check). | 多モデル判定の合議（異系統） | academia | primary | fact | 2026-05-28 | verified | [Nine Judges, Two Effective Votes: Correlated Errors Undermine LLM Evaluation Panels](https://arxiv.org/abs/2605.29800) |
| C4 | Across 350+ LLMs, when two models were both wrong on a benchmark they chose the same wrong answer about 60% of the time, and shared architectures and providers were identified as drivers of this correlation (against candidate 2, where a different-family reviewer is assumed to share fewer errors). | モデル間の誤りの相関 | academia | primary | fact | 2025-06-09 | verified | [Correlated Errors in Large Language Models](https://arxiv.org/abs/2506.07962) |
| C5 | Cognition's 2025 post argues that parallel agents whose actions rest on unstated, conflicting assumptions produce inconsistent work, and that this is a reason to keep decisions in one thread (this supports candidate 1 and works against any design that splits decision-making across agents). | 単一スレッドの実行役 | practitioners | primary | opinion | 2025-06-12 | verified | [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) |

## 抜粋

- C1：「These changes cut research time by up to 90% for complex queries.」
- C2：「about 15× more tokens than chats」
- C3：「we find that the 9 judges effectively provide only about 2 independent votes' worth of information.」
- C4：「models agree 60% of the time when both models err」
- C5：「The actions subagent 1 took and the actions subagent 2 took were based on conflicting assumptions not prescribed upfront.」

## 確かめられなかったこと

- 候補 1 の反証（書き込みを 1 か所に集めると速度が落ちる・誤りを見逃す）について、一次の事後検証や実測は見つからなかった。Cognition の記事は、大きな課題で文脈窓があふれることを限界として認めているが、本文の一字一句の抜粋を取れなかったため claims に入れていない。
- 候補 2 について、別系統の検めが同じ誤りを共有するという証拠（Kim et al. 2025、Nine Judges 2026）は見つかったが、人の作業の記録や決定の記録が形だけになったという実測は見つからなかった。ジャッジの研究はコードレビューや設計判断の記録と同じ条件ではない点に注意が必要。
- 候補 3 について、承認の抜き取り監査が重大な誤りを通すという一次の実測は見つからなかった。「Claude Code の利用者はプロンプトの 93% を承認する」という数字は出典の確認ができず、根拠の無い数字として扱うべき。人の自動化への過信（automation complacency）の古典的研究（NASA 技術報告 1993、Wickens et al. 2015、Manzey 系の 2006 と 2008 の研究）は検索結果の要約しか得られず、本文の抜粋を取れなかった。PubMed 25886768 の抄録は取得できなかった。
- 候補 3 について、AI 運用の事故（AWS の Kiro による 13 時間の停止）は、FT の報道を経由した二次の記事でしか確かめられない。Amazon は利用者の誤設定だと主張して争っており、内部情報源の話は未確認。事後報告の本文は見つからなかった。
- 候補 4 について、細部を見ない管理役が誤った方向を出した一次の事例は見つからなかった。人の管理の遠さに関する研究も、AI の文脈では見つからなかった。
- Pombal et al. 2026（arXiv 2604.06996）の self-preference の研究は、本文を取得していないため使っていない。

## 検索語

- LLM evaluator self-preference bias favors its own generated outputs study
- correlated errors across large language models shared failures judge ensemble
- approval fatigue users rubber-stamp AI agent permission prompts study
- Cognition "Don't Build Multi-Agents" single-threaded agent context
- automation complacency human oversight missed errors autonomous systems review study
- postmortem agent deployed without human review caused outage incident AI coding agent production

## 問い

# 決定の権限と役の分け方の案への反証（反証）

## 段
refute

## 決めたい判断
1 人の人間（操縦士）が AI のエージェントに開発を任せる環境で、次の形を暫定の枠組みとして採るか

## 判断を変えうるもの
下の候補に反する一次の出典（失敗の事後検証・実測・研究）があれば、候補を書き直すか条件を足す

## 候補
1. 書き込み（コードと文書の変更）は 1 つの実行役に集め、実行役は自分の採点・検証・物差しに関わる決定を確定しない
2. 決定は記録（問い・比べた案・見た出典・決めた者・戻せるか）に残し、検め役（実行役と文脈を共有しない別系統のモデル）が記録と差分に助言する。検め役は書き込まない
3. 人は、戻せない決定・目的と物差しの変更・実行役の利害が絡む決定だけを、記録を読んで決める。それ以外は事後の抜き取りで監査し、いつでも止められる位置にいる
4. 総監督（別の文脈のエージェント）は、実行の細部を見ずに、決定の記録と実測の表だけを読んで方向の案を出す

## 問い
上の候補のそれぞれに**反する**証拠だけを集めてください。

- 1：書き込みを 1 か所に集めることで、誤りが見逃される・速度が落ちるという指摘
- 2：別系統のモデルの検めも偏る・役に立たない・同じ誤りを共有するという実測。決定の記録が形だけになる例
- 3：承認を絞ると重大な誤りを通すという実測。事後の監査が効かない例
- 4：細部を見ない判断役が誤った方向を出す例（現場から離れた管理の失敗）
