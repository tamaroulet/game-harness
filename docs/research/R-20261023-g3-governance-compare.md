# 1 人と AI のエージェントの開発環境の、決定の権限と役の分け方の比較（比較）

- 記録：R-20261023-g3-governance-compare
- 段：compare
- 決めたい判断：1 人の人間（操縦士）が AI のエージェントに開発を任せる環境で、決定の権限と役をどう分けるか。今は 1 つのエージェント（実行役）が判断・実行・検証を握り、人の承認は PR の番号を言うだけで、根拠は人に見えない
- 調べた日時：2026-10-23T16:49:38+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 15・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic の自社マルチエージェント研究システムの実測では、エージェントは通常のチャットの約 4 倍、マルチエージェントは約 15 倍のトークンを使う。調整の費用は主にトークンとして現れる。 | 役割分担（複数エージェント） | frontier_labs | primary | fact | 不明 | verified | [How we built our multi-agent research system (Anthropic Engineering)](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C2 | 同じ Anthropic の投稿は、性能の分散の 80% がトークン使用量だけで説明できたと報告している。複数エージェントの利得の一部は、より多くの計算を使ったことによる可能性がある。 | 役割分担（複数エージェント） | frontier_labs | primary | fact | 不明 | verified | [How we built our multi-agent research system (Anthropic Engineering)](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C3 | Anthropic は、マルチエージェントが引き合うのは、課題の価値が増えた費用を払えるほど高い場合に限ると述べている。常時の運用には費用の条件が付く。 | 役割分担（複数エージェント） | frontier_labs | primary | fact | 不明 | verified | [How we built our multi-agent research system (Anthropic Engineering)](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C4 | Anthropic は、全エージェントが同じ文脈を共有する必要がある領域や、並列にできる部分が少ない領域（コーディングの多く）は、マルチエージェントに向かないと述べ、エージェントが実時間で他のエージェントへ委任・調整するのはまだ得意でないと認めている。 | 役割分担（複数エージェント） | frontier_labs | primary | opinion | 不明 | verified | [How we built our multi-agent research system (Anthropic Engineering)](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C5 | Cognition（Devin）は、文脈と完全な実行の履歴を共有しないと、行動に含まれる暗黙の決定が食い違い、悪い結果になると主張し、既定では単一スレッドの線形エージェントを選ぶべきだとする。 | 単一スレッド（1 エージェントに集約） | practitioners_at_scale | primary | opinion | 2025-06-12 | verified | [Don't Build Multi-Agents (Cognition)](https://cognition.com/blog/dont-build-multi-agents) |
| C6 | Cognition は、原則に従う最も簡単な方法は単一スレッドの線形エージェントだと述べている。ただし文脈の窓が溢れる限界があり、長い作業には圧縮用のモデルが要ると続けている。 | 単一スレッド（1 エージェントに集約） | practitioners_at_scale | primary | opinion | 2025-06-12 | verified | [Don't Build Multi-Agents (Cognition)](https://cognition.com/blog/dont-build-multi-agents) |
| C7 | Cognition は 2026 年 4 月の続報で、書き込みを単一スレッドに保ち、ほかのエージェントは行動でなく知恵を出す形が今は最もうまくいくと述べた。判断役を分けても実行（書き込み）は 1 か所に集める形に近い。 | 書き込み単一＋助言役の分離 | practitioners_at_scale | primary | opinion | 2026-04 | verified | [Multi-Agents: What's Actually Working (Cognition)](https://cognition.com/blog/multi-agents-working) |
| C8 | Cognition の続報は、書いたエージェントと検めるエージェントが事前に文脈を共有しないときに、検めが最も効くと報告する。Devin Review は 1 つの PR あたり平均 2 件の不具合を見つけ、約 58% が重大だという（自社の数字）。 | 検めの分離（別文脈のレビュー役） | practitioners_at_scale | primary | vendor_claim | 2026-04 | verified | [Multi-Agents: What's Actually Working (Cognition)](https://cognition.com/blog/multi-agents-working) |
| C9 | Cognition の続報は、エージェントの使用の急増が費用の急増を招いたこと、親子のエージェントで共有の状態があると思い込む失敗が起きることを挙げる。調整の費用と食い違いの実例にあたる。 | 役割分担（複数エージェント） | practitioners_at_scale | primary | fact | 2026-04 | verified | [Multi-Agents: What's Actually Working (Cognition)](https://cognition.com/blog/multi-agents-working) |
| C10 | ICLR 2024 の論文は、外部の帰還が無いと LLM は自分の答えを直すのが難しく、自己修正の後に性能がむしろ下がることがあると示した。自己検めの限界の根拠になる。 | 自己修正の限界（検めの分離を支える） | academia | primary | fact | 2023-10-03 | verified | [Large Language Models Cannot Self-Correct Reasoning Yet](https://arxiv.org/abs/2310.01798v2) |
| C11 | 同じ論文は、複数エージェントの討論も、同数の回答を使った自己一貫性より良くないと報告する。別のエージェントを足すだけでは検めの質は上がらず、独立した外部の情報が要る。 | 自己修正の限界（検めの分離を支える） | academia | primary | fact | 2024-03-14 | verified | [Large Language Models Cannot Self-Correct Reasoning Yet (HTML v2)](https://arxiv.org/html/2310.01798v2) |
| C12 | NeurIPS 2024 の論文は、LLM の評価役が自分の出力を高く採点する偏り（自己選好）を示し、自己認識の力と自己選好の強さに線形の相関があると報告した。実行役と同じ系統のモデルに検めさせる案への反対の証拠になる。実験は要約の課題に限る。 | 検めの分離（別系統のモデル） | academia | primary | fact | 2024-04-15 | verified | [LLM Evaluators Recognize and Favor Their Own Generations](https://arxiv.org/abs/2404.13076v1) |
| C13 | MAST の研究（UC Berkeley 主導）は、7 つの代表的な枠組みの 1,600 以上の実行記録から、マルチエージェントの失敗を 14 の型・3 つの群（設計の問題・エージェント間の食い違い・検証の問題）に整理した。ベンチマークでの利得は単一エージェントに比べ小さいことが多い。 | 役割分担（複数エージェント） | academia | primary | fact | 2025-03-17 | verified | [Why Do Multi-Agent LLM Systems Fail?](https://arxiv.org/abs/2503.13657v3) |
| C14 | Anthropic は Claude Code で、手動の許可確認では利用者が 93% を承認してしまい、承認疲れで注意が落ちると述べた。承認の数が多いほど承認が形だけになるという当事者の実測にあたる。 | 人の承認の削減（重さによる段階化・自動分類） | frontier_labs | primary | fact | 不明 | verified | [Claude Code auto mode (Anthropic Engineering)](https://anthropic.com/engineering/claude-code-auto-mode) |
| C15 | 同じ投稿は、承認の代わりに置いた分類器の誤りの数字も出している。実際の過剰な行動の見逃しが全体で 17%、段階 1 では 6.6%。重さで承認を絞る案（候補 2）でも、自動の振り分けには見逃しが残る。 | 人の承認の削減（重さによる段階化・自動分類） | frontier_labs | primary | fact | 不明 | verified | [Claude Code auto mode (Anthropic Engineering)](https://anthropic.com/engineering/claude-code-auto-mode) |

## 抜粋

- C1：「multi-agent systems use about 15× more tokens than chats.」
- C2：「We found that token usage by itself explains 80% of the variance」
- C3：「multi-agent systems require tasks where the value of the task is high enough to pay for the increased performance.」
- C4：「most coding tasks involve fewer truly parallelizable tasks than research」
- C5：「Actions carry implicit decisions, and conflicting decisions carry bad results」
- C6：「The simplest way to follow the principles is to just use a single-threaded linear agent:」
- C7：「multi-agent systems work best today when writes stay single-threaded」
- C8：「we found this technique to work best when the coding and review agents do not share any context beforehand.」
- C9：「Agents assume they share state with their children when they don't.」
- C10：「LLMs struggle to self-correct their responses without external feedback」
- C11：「its efficacy is no better than self-consistency」
- C12：「a linear correlation between self-recognition capability and the strength of self-preference bias」
- C13：「the first multi-agent system dataset to outline the failure dynamics in MAS」
- C14：「Manual prompts sit in the middle, and in practice users accept 93% of them anyway.」
- C15：「The 17% false-negative rate on real overeager actions is the honest number.」

## 確かめられなかったこと

- quote の字句は WebFetch（小型モデルが要約して返す）経由で取った。原文との一字一句の一致は機械の照合で確かめる必要がある。Anthropic の 2 本の公開日は取れず「不明」にした
- candidate 3（人がすべての PR と計画を承認する）の承認疲れについて、PR や計画の承認に限った一次の実測は見つからなかった。近いのは Claude Code の許可確認（93%）だけで、PR 承認とは対象が違う
- 自動化バイアスの一次の実験（論文）は取れなかった。見つかったのはブログ・二次の紹介（Codacy、tianpan.co など）と、出典が不確かな研究（He ら 802 人・196,212 PR、arXiv 2608.26316）だけで、原文を確かめられず claims に入れなかった
- 8 月の「許可の 97% を承認」「人は危険な命令の 13.6% しか見つけない」は報道の数字で、一次の出典に当たれず claims に入れなかった（自社の研究とされ、売り手の主張の可能性がある）
- 役を分ける案（総監督・実行役・別系統の検め役）を 1 人の運用で回した実測、人の時間の実測、判断と実行を分けた場合の決定の食い違いの実測は見つからなかった。MAST と Cognition は複数エージェント一般の話で、「判断役」と「実行役」の分離そのものを測っていない
- 別系統のモデルが自己選好を避けられるかを直接測った一次の実測は取れなかった。Panickssery らは同系統の偏りを示すにとどまる。Cognition の「文脈を共有しない検め役」は同じ系統のモデルの別文脈で、別系統ではない
- 自己修正の論文は推論の課題、自己選好の論文は要約の課題が対象で、開発の決定の記録・提案の評価にそのまま当てはまるかは出典から確かめられない
- 候補 1（記録を足して人に見せるだけ）を単独で評価した出典は見つからなかった。決定の記録が承認の質を上げるかの実測は無い
- 分野 standards_tools（OpenSSF・GitHub の文書など）と practitioners（名の通った実務家の当人の記事）は今回巡回しておらず、この比較には含まれていない

## 検索語

- Cognition "Don't Build Multi-Agents" context engineering single-threaded
- Anthropic "How we built our multi-agent research system" 15x tokens
- Large Language Models Cannot Self-Correct Reasoning Yet arXiv
- LLM evaluators recognize and favor their own generations self-preference arXiv
- Anthropic Claude Code auto mode users approve 93% of permission prompts approval fatigue
- Why Do Multi-Agent LLM Systems Fail? MAST taxonomy arXiv
- automation bias human oversight AI review rubber-stamp study code review LLM-generated pull requests reviewers

## 問い

# 1 人と AI のエージェントの開発環境の、決定の権限と役の分け方の比較（比較）

## 段
compare

## 決めたい判断
1 人の人間（操縦士）が AI のエージェントに開発を任せる環境で、決定の権限と役をどう分けるか。今は 1 つのエージェント（実行役）が判断・実行・検証を握り、人の承認は PR の番号を言うだけで、根拠は人に見えない

## 判断を変えうるもの
候補のどれかが、1 人の運用では回らない（人の負荷・費用・調整の失敗）、または承認や検証を形だけにしてしまう、という一次の出典や実測があれば外すか条件を足す

## 候補
1. 今の形を保ち、決定の記録（問い・比べた案・見た出典・決めた者・戻せるか）を足して人に見せるだけにする
2. 役を分ける：判断と提案は総監督（別の文脈）、実行は実行役（判断を下さない）、検めは別系統のモデル（自分の出力を評価しない）。決定は重さで段階に分け（戻せる・戻せない・物差しや目的を変える・判断役の利害が絡む）、人は戻せない決定と物差しの変更だけを、記録を読んで決める。それ以外は事後の抜き取りで監査する
3. 1 つのエージェントに全部を任せ（単一スレッド）、人がすべての PR と計画を承認する

## 問い
上の 3 つの候補を、次の観点で比べてください。各観点について一次の出典を挙げてください。

- 判断の質（自己評価の偏り・自己修正の限界・確証バイアス）
- 人の承認が意味を持つか（承認疲れ・自動化バイアス・承認の数）
- 調整の費用と失敗（文脈の共有・決定の食い違い・トークン）
- 1 人の運用で回るか（人の時間・費用）
