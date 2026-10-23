# AI のエージェントに開発を任せる環境での、決定の権限・人の監督・承認の形（俯瞰）

- 記録：R-20261023-g1-decision-rights-map
- 段：map
- 決めたい判断：1 人の人間（操縦士）が、AI のエージェント（実行役・総監督・調べ役・実装役）に開発を任せる環境で、どの決定を誰が下し、誰が検め、根拠をどう見える形で残すか。今の形は次の点で破綻している：実行役が判断と実行と検証を一人で握る、人の承認が PR の番号を言うだけの儀式になっている、判断の根拠が人に見えない、権限が「AI か人か」の二択で切られている
- 調べた日時：2026-10-23T16:47:07+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 16・quote_not_found 1・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Feng らは、エージェントの自律の段階を、使う人が担う役割（operator・collaborator・consultant・approver・observer）で区切る 5 段階として示した。自律の段階は設計上の決定として選べる、と論じている。 | 人の役割で切る自律の段階 | academia | primary | fact | 2025-06-14 | verified | [Levels of Autonomy for AI Agents (Feng, McDonald, Zhang)](https://arxiv.org/abs/2506.12469) |
| C2 | EU AI Act 14 条は、人の監督の手段を、リスク・自律の度合い・使われ方に見合わせるよう求めている。 | リスクと自律に比例する監督（規制） | standards_tools | primary | fact | 不明 | verified | [EU AI Act Article 14: Human oversight](https://artificialintelligenceact.eu/article/14/) |
| C3 | EU AI Act 14(4)(b) は、監督する人が出力を自動的に信用しすぎる傾向（自動化バイアス）を自覚し続けられるようにすることを求めている。 | 自動化バイアスの自覚を義務づける規制 | standards_tools | primary | fact | 不明 | verified | [EU AI Act Article 14: Human oversight](https://artificialintelligenceact.eu/article/14/) |
| C4 | EU AI Act 14(4)(e) は、監督する人が運転に介入するか、停止ボタンで止められることを求めている。 | リスクと自律に比例する監督（規制） | standards_tools | primary | fact | 不明 | quote_not_found | [EU AI Act Article 14: Human oversight](https://artificialintelligenceact.eu/article/14/) |
| C5 | NIST AI RMF の GOVERN 3.2 は、人と AI の組み合わせ方ごとに役割と責任を定めて区別する方針と手順を置くことを求めている。 | 役割と責任を定める管理の枠組み | standards_tools | primary | fact | 不明 | verified | [NIST AI RMF Playbook: Govern](https://airc.nist.gov/AI_RMF_Knowledge_Base/Playbook/Govern) |
| C6 | Anthropic は、Claude Code のユーザーが許可の確認の 97% を承認していると報告している。個々の許可の却下は 3%、計画の承認では 39% を却下する。 | 承認の形骸化の実測 | frontier_labs | primary | fact | 2026-08-07 | verified | [Auto mode is now the default in Claude Code](https://claude.com/blog/auto-mode-default-in-claude-code) |
| C7 | Anthropic の対照実験では、人の確認が危険なコマンドを検出できたのは 13.6%（1,053 人中 143 人）だった。分類器を使う auto mode は同じコマンドの 89% を止めた。 | 承認の形骸化の実測 | frontier_labs | primary | vendor_claim | 2026-08-07 | verified | [Auto mode is now the default in Claude Code](https://claude.com/blog/auto-mode-default-in-claude-code) |
| C8 | Anthropic は、サンドボックスで許可の確認を 84% 減らせたと報告している。承認を押し続けると承認疲れになる、とも書いている。 | 承認の数を減らす（境界を先に決める） | frontier_labs | primary | vendor_claim | 2025-10-20 | verified | [Beyond permission prompts: making Claude Code more secure and autonomous](https://www.anthropic.com/engineering/claude-code-sandboxing) |
| C9 | Anthropic は、有効な監督とは全ての行動を承認することではなく、肝心な時に介入できる位置にいることだ、と述べている。すべての行動の承認を定める要件は、安全に結びつかない摩擦を生むとも述べている。 | 介入できる位置を保つ監督 | frontier_labs | primary | opinion | 2026-02-18 | verified | [Measuring AI agent autonomy in practice](https://www.anthropic.com/research/measuring-agent-autonomy) |
| C10 | Anthropic の利用実測では、経験を積んだユーザーほど完全自動承認を多く使う（新規は約 20%、750 セッションを超えると 40% 超）。 | 承認の形骸化の実測 | frontier_labs | primary | fact | 2026-02-18 | verified | [Measuring AI agent autonomy in practice](https://www.anthropic.com/research/measuring-agent-autonomy) |
| C11 | Anthropic の指針は、コードやシステムを変える行動の前に人の承認を求め、計画を人に見える形（作業リスト）で示す設計を述べている。 | 計画の可視化つきの承認 | frontier_labs | primary | fact | 2025-08-04 | verified | [Our framework for developing safe and trustworthy agents](https://www.anthropic.com/news/our-framework-for-developing-safe-and-trustworthy-agents) |
| C12 | GitHub の Copilot cloud agent は、エージェントに PR を作らせた本人がその PR を承認することを防ぐ。書き込み権限を持つ人が承認するまで、GitHub Actions のワークフローは走らない。 | 依頼者と承認者の分離 | standards_tools | primary | fact | 不明 | verified | [Risks and mitigations for GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations) |
| C13 | GitHub は、エージェントのセッションログは作業と使った道具を示すが、人自身の確認とテストの代わりにはならない、としている。 | 根拠の記録（ログ）と人の確認の併用 | standards_tools | primary | fact | 不明 | verified | [About GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent) |
| C14 | Bezos は、取り返しのつかない決定（one-way door）は慎重に時間をかけて決め、戻せる決定（Type 2）は速く決めるよう分けている。重い手順を全部に当てると遅くなる、という警告も述べている。 | 戻せるかで決定を分ける（one-way/two-way door） | practitioners_at_scale | secondary | opinion | 不明 | verified | [Reversible and irreversible decisions (Farnam Street, Amazon 2015 letter を引用)](https://fs.blog/reversible-irreversible-decisions/) |
| C15 | Nygard の ADR は、重要な決定を、力関係と 1 つの決定を書いた短い記録にして残す。決定を覆すときは古い記録を残し、superseded（差し替え済み）の印を付ける。 | 決定の記録（ADR） | practitioners | primary | fact | 2011-11-15 | verified | [Documenting Architecture Decisions (Michael Nygard)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) |
| C16 | Turan のプレプリントは、レビューする人が量に応じて疲れるとモデル化すると、人の監督を増やすほうが安全でなくなりうる、と示している。負荷を見て人に回す量を決める方針と、疲れた人に悪い操作を通す洪水攻撃への備えを提案している。 | 人の負荷を考えた承認の量の調整 | academia | secondary | fact | 2026-06-08 | verified | [Oversight Has a Capacity: Calibrating Agent Guards to a Subjective, Fatiguing Human](https://arxiv.org/abs/2606.08919) |
| C17 | DeepMind の「Intelligent AI Delegation」は、AI への委任を、権限・責任・説明責任の移譲を含む決定の連なりとして枠づける。 | 権限・責任・説明責任の移譲としての委任 | frontier_labs | primary | fact | 2026-02-12 | verified | [Intelligent AI Delegation (Tomašev, Franklin, Osindero)](https://arxiv.org/abs/2602.11865) |

## 抜粋

- C1：「We argue that an agent's level of autonomy can be treated as a deliberate design decision」
- C2：「The oversight measures shall be commensurate with the risks, level of autonomy and context of use of the high-risk AI system」
- C3：「to remain aware of the possible tendency of automatically relying or over-relying on the output」
- C4：「to intervene in the operation of the high-risk AI system or interrupt the system through a 'stop' button」
- C5：「Policies and procedures are in place to define and differentiate roles and responsibilities for human-AI configurations」
- C6：「users approve 97% of permission prompts in Claude Code.」
- C7：「the testers caught the dangerous command just 13.6% of the time」
- C8：「In our internal usage, we've found that sandboxing safely reduces permission prompts by 84%.」
- C9：「effective oversight doesn’t require approving every action but being in a position to intervene when it matters.」
- C10：「Newer users (<50 sessions) employ full auto-approve roughly 20% of the time」
- C11：「Humans need visibility into agents' problem-solving processes.」
- C12：「Prevents the user who asked Copilot cloud agent to create a pull request from approving it.」
- C13：「Logs do not replace your own review and testing.」
- C14：「Type 2 decisions can and should be made quickly by high judgment individuals or small groups.」
- C15：「If a decision is reversed, we will keep the old one around, but mark it as superseded.」
- C16：「more human oversight can make a system less safe」
- C17：「incorporates transfer of authority, responsibility, accountability」

## 確かめられなかったこと

- practitioners_at_scale：エージェントを大規模に運用する企業の当事者の技術ブログや事後検証は、取れなかった。Amazon の one-way/two-way door の出典は、株主への手紙の原文ではなく引用記事（fs.blog）で、AI のエージェント運用に当てた例ではない。
- OpenAI の Practices for Governing Agentic AI Systems（2023 年 12 月とされる）は、公式ページが 403、PDF は本文を取り出せず、本文の引用を取れなかった。人の承認の関門や行動の記録の記述は確認できていない。
- Amazon の 2015・2016 年の株主への手紙の原文は、aboutamazon.com の URL が 404 または該当部分なしで、確認できなかった。
- ISO/IEC 42001 の条文、RACI・DACI をエージェントの運用に当てた例、OpenSSF の文書は、探さなかった。
- NIST の GOVERN 3.2 の引用は取得ツールの要約を経ており、文が途中で切れている可能性がある。
- 自動化バイアスの古典的な研究（Parasuraman と Manzey など）と、承認疲れを AI エージェントの承認で測った査読つきの研究は、取れなかった。Turan のプレプリントは査読の有無が不明で、モデル化の結果である。
- Anthropic の 97% と 13.6% は自社の製品の宣伝を兼ねた記事で、auto mode を売る文脈にある。ほかの記事に出る 93% は、一次の出典では見つからなかった。
- quote は WebFetch の要約を通して得たため、出典の本文との一字一句の一致は未確認である。照合が必要。
- 決定の記録の実践のうち、エージェントの判断の根拠を出典つきで残す方法の実例は、ADR（人向け）と GitHub のセッションログ以外は取れなかった。

## 検索語

- Anthropic Claude Code auto mode approval fatigue users approve 93% of permission prompts
- OpenAI Practices for Governing Agentic AI Systems approval human oversight rubber-stamp
- Amazon 2015 shareholder letter Bezos "one-way door" Type 1 decisions irreversible "Type 2"
- automation bias rubber-stamping human oversight AI agents approval fatigue study arXiv human-in-the-loop
- Intelligent AI Delegation Google DeepMind arXiv authority responsibility accountability delegation framework

## 問い

# AI のエージェントに開発を任せる環境での、決定の権限・人の監督・承認の形（俯瞰）

## 段
map

## 決めたい判断
1 人の人間（操縦士）が、AI のエージェント（実行役・総監督・調べ役・実装役）に開発を任せる環境で、どの決定を誰が下し、誰が検め、根拠をどう見える形で残すか。今の形は次の点で破綻している：実行役が判断と実行と検証を一人で握る、人の承認が PR の番号を言うだけの儀式になっている、判断の根拠が人に見えない、権限が「AI か人か」の二択で切られている

## 判断を変えうるもの
最前線の企業・規格・研究が、エージェントの決定の権限を、決定の重さ（戻せるか）・利害の相反・根拠の見えやすさで段階に分けている例や、人の承認が形骸化する（自動化バイアス・承認疲れ）証拠と対策があれば、それを暫定の形に採る

## 分野
frontier_labs practitioners_at_scale academia standards_tools practitioners

## 問い
次を、分野ごとに一次の出典で集めてください。

- エージェントの自律の段階（levels of autonomy）と、段階ごとの人の役割（提案を受ける・承認する・監督する・事後に監査する）を定めた枠組み（提供元の指針、NIST AI RMF、ISO/IEC 42001、EU AI Act の人の監督の条文など）
- 決定を重さで分ける実践（戻せる決定と戻せない決定、Amazon の one-way/two-way door、RACI・DACI、ADR と決定の記録）を、AI のエージェントの運用に当てた例
- 人の承認が形だけになる問題（自動化バイアス、承認疲れ、rubber-stamping）の研究と、承認を意味あるものにする対策（根拠の提示、承認の前に読む材料、抜き取り、承認の数を減らす）
- エージェントの判断の根拠を人が後から辿れるようにする実践（決定の記録、出典つきの根拠、監査の記録）
