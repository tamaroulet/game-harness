# 自律的なソフトウェア開発の全体の構成：役の分け方と、長く走るエージェントの回し方

- 記録：R-20261023-m1-autonomous-dev-architecture
- 段：map
- 決めたい判断：自律開発環境（計画・実装・検証・統合を、人の承認を最小にして回す仕組み）の上位の設計を、どの系統に寄せるか。いまの独自の構成（計画役・実装役・総監督・統合 PR を回す常駐のループ）を残すか、既製の道具の上に作り直すか
- 調べた日時：2026-10-23T14:01:56+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 14・quote_not_found 5・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic は長く走るエージェントを「初期化役の最初のセッション」と「以後の増分セッション」に分けている。各セッションは新しい文脈で始まり、終わるときに構造化した更新を残す。 | 単一エージェントの多セッション引き継ぎ | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C2 | 状態の引き継ぎは、進捗ファイル（claude-progress.txt）と git 履歴の組み合わせで行う。新しい文脈のエージェントは、これで作業の状態をすぐ把握する。 | 単一エージェントの多セッション引き継ぎ | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C3 | 機能リスト（JSON）は、エージェントが passes 欄の状態だけを書き換えてよい決まりにしている。テストの削除や編集は禁じている。 | 単一エージェントの多セッション引き継ぎ | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C4 | 長時間の単一エージェントの失敗例として、実装の途中で文脈が尽きること、十分に試さずに機能を完了と宣言することが挙げられている。git は悪い変更を戻して動く状態へ復旧するのに使う。 | 単一エージェントの多セッション引き継ぎ | frontier_labs | primary | fact | 2025-11-26 | verified | [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) |
| C5 | Anthropic の調査系エージェントは、リード役が計画して並列の下位エージェントに仕事を渡す形（オーケストレーターとワーカー）を取る。トークン消費は、チャットの約 15 倍になる。 | オーケストレーターと下位エージェント | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C6 | Anthropic は、エラー時に最初からやり直さず、止まった所から再開できる仕組みを作った。再開にはチェックポイントと再試行を併用する。 | オーケストレーターと下位エージェント | frontier_labs | primary | fact | 2025-06-13 | verified | [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) |
| C7 | Cursor は大規模な自律コーディングで、計画役（再帰的に下位計画役を持てる）が仕事を作り、作業役が黙々と処理する階層構造にした。各周期の終わりに審判役が続行を判断し、次の周期は新しい文脈で始まる。 | 計画役・作業役・審判役の階層 | practitioners_at_scale | primary | fact | 2026-01-14 | verified | [Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents) |
| C8 | Cursor では、ロックで調整する平らな構成が詰まり、20 体のエージェントが実効 2〜3 体分の処理量に落ちた。責任を持つ者がいないと、難しい仕事は避けられた。 | 計画役・作業役・審判役の階層 | practitioners_at_scale | primary | fact | 2026-01-14 | verified | [Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents) |
| C9 | Cursor 自身が限界を認めている。ずれや視野の狭まりを防ぐために定期的な新規開始が要り、エージェントが長く走りすぎることもある。 | 計画役・作業役・審判役の階層 | practitioners_at_scale | primary | opinion | 2026-01-14 | verified | [Scaling long-running autonomous coding](https://cursor.com/blog/scaling-agents) |
| C10 | Cognition は、複数エージェントの協調は壊れやすいと主張する。原則は、全トレース付きで文脈を共有することと、行動が暗黙の決定を含むため決定の衝突が悪い結果を生むことの 2 つ。単一の線形なエージェントを勧める立場。 | 単一エージェント（反マルチエージェント） | practitioners_at_scale | primary | opinion | 2025-06-12 | verified | [Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents) |
| C11 | Claude Code の公式の使い方は、claude -p（ヘッドレス）と Agent SDK。Agent SDK は Claude Code と同じ道具・エージェントのループ・文脈管理を提供する。CLI は スクリプトと CI/CD 向け、Python と TypeScript の SDK は完全な制御向け。 | ヘッドレス実行と SDK | frontier_labs | primary | fact | 不明 | quote_not_found | [Run Claude Code programmatically](https://code.claude.com/docs/en/headless) |
| C12 | ヘッドレス実行は、セッション ID で会話を再開できる（--resume、--continue）。JSON 出力に費用の見積りが入り、--max-budget-usd で上限を置ける。 | ヘッドレス実行と SDK | frontier_labs | primary | fact | 不明 | quote_not_found | [Run Claude Code programmatically](https://code.claude.com/docs/en/headless) |
| C13 | Claude Code GitHub Actions は、@claude の言及で動く対話モードと、prompt を与えて任意の GitHub イベント・cron で動く自動モードを持つ。issue から PR を作る使い方が公式に書かれている。 | GitHub 統合（CI 内のエージェント） | frontier_labs | primary | fact | 不明 | verified | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C14 | Claude Code GitHub Actions では、bot が起動者のとき既定で拒否される。bot 同士のループを防ぐためで、許可は allowed_bots に列挙する。費用の上限は --max-turns・タイムアウト・並行数制御で抑える。 | GitHub 統合（CI 内のエージェント） | standards_tools | primary | fact | 不明 | quote_not_found | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C15 | OpenAI の Codex にも同じ形がある。codex exec は CI などスクリプトからの非対話実行で、resume で前回の実行を続けられ、GitHub Actions では公式のアクションを推奨している。 | ヘッドレス実行と SDK | frontier_labs | primary | fact | 不明 | quote_not_found | [Non-interactive mode (Codex)](https://learn.chatgpt.com/docs/non-interactive-mode) |
| C16 | GitHub Copilot の coding agent は、issue の割り当てを契機に、GitHub Actions 上の環境で動く。進捗は下書き PR に積まれ、完了すると人にレビューを依頼する。Actions の実行には人の承認が要る。 | クラウドのバックグラウンドのエージェント（issue→PR） | frontier_labs | primary | fact | 2025-05-19 | quote_not_found | [GitHub Copilot: Meet the new coding agent](https://github.blog/ai-and-ml/github-copilot/github-copilot-meet-the-new-coding-agent/) |
| C17 | arXiv の比較研究（2025-11）は 7 つの汎用エージェント枠組みを、開発・脆弱性検出・修復で評価した。全体の有効性は中程度で、AgentOrchestra は調整の負荷で軌跡が長くなり、GPTswarm が費用効率で最良だった。この主張は取得時の要約によるもので、本文の一字一句の引用は確認できていない。 | 枠組みの実測比較 | academia | primary | fact | 2025-11-02 | verified | [A Comprehensive Empirical Evaluation of Agent Frameworks on Code-centric Software Engineering Tasks](https://arxiv.org/abs/2511.00872) |
| C18 | SWE-bench の提出物を調べた研究（2025-06、改訂 2026-02）は、エージェント型と非エージェント型の両方の設計が存在することを報告した。検索で出た要約には、単一と複数のエージェントの優劣は決められないとあるが、ページ本文では確認できていない。 | 枠組みの実測比較 | academia | primary | fact | 2025-06-20 | verified | [Dissecting the SWE-Bench Leaderboards](https://arxiv.org/abs/2506.17208) |
| C19 | Liu らのサーベイ（arXiv 2409.02977）は、ソフトウェア工学向けの LLM エージェントを扱い、複数エージェントの役割・協調の仕組みなども整理している。この主張は検索結果の要約によるもの。 | サーベイ | academia | primary | fact | 2024-09-04 | verified | [Large Language Model-Based Agents for Software Engineering: A Survey](https://arxiv.org/abs/2409.02977) |

## 抜粋

- C1：「Every subsequent session asks the model to make incremental progress, then leave structured updates.」
- C2：「The key insight here was finding a way for agents to quickly understand the state of work when starting with a fresh context window, which is accomplished with the claude-progress.txt file alongside the git history.」
- C3：「It is unacceptable to remove or edit tests because this could lead to missing or buggy functionality.」
- C4：「Claude’s tendency to mark a feature as complete without proper testing.」
- C5：「multi-agent systems use about 15× more tokens than chats」
- C6：「we built systems that can resume from where the agent was when the errors occurred」
- C7：「At the end of each cycle, a judge agent determined whether to continue, then the next iteration would start fresh.」
- C8：「Twenty agents would slow down to the effective throughput of two or three, with most time spent waiting.」
- C9：「We still need periodic fresh starts to combat drift and tunnel vision.」
- C10：「Share context, and share full agent traces, not just individual messages」
- C11：「The [Agent SDK](/docs/en/agent-sdk/overview.md) gives you the same tools, agent loop, and context management that power Claude Code.」
- C12：「Use `--continue` to continue the most recent conversation, or `--resume` with a session ID to continue a specific conversation.」
- C13：「Use it to turn issues into pull requests, fix bugs from a comment, or automate recurring tasks.」
- C14：「Set `--max-turns` in `claude_args` to limit iterations」
- C15：「If you need to continue a previous run (for example, a two-stage pipeline), use the `resume` subcommand:」
- C16：「GitHub Actions workflows won’t run without your approval, giving you the chance to spot-check the agent’s code.」
- C17：「A Comprehensive Empirical Evaluation of Agent Frameworks on Code-centric Software Engineering Tasks」
- C18：「the presence of both agentic and non-agentic designs」
- C19：「The recent advance in Large Language Models (LLMs) has shaped a new paradigm of AI agents, i.e., LLM-based agents.」

## 確かめられなかったこと

- OpenAI の「harness engineering」（2026-02-11）は本文を取得できず（HTTP 403）、claims に入れていない。二次の記事は検索結果の要約でしか見ていない。
- Codex のクラウド実行と Codex SDK の公式文書は取得できなかった。クラウド実行の説明は第三者の資料でしか見ていない。
- Google DeepMind・Meta・Microsoft の自律開発エージェント（Jules、ADK など）の一次情報は調べていない。
- Claude Code GitHub Actions・ヘッドレス実行・Codex の文書は公開日が表示されていない。Copilot の GitHub ブログ（2025-05）は、新しい文書で変わっている可能性がある。
- 長く走る自律開発（数日〜）を、標準の道具だけで回している第三者の事後検証は見つからなかった。Cursor の例は自社製の枠組みで、標準の SDK で組んだ例ではない。
- MetaGPT・ChatDev など役割分担型の研究は、単一エージェントとの比較の数字が見つからず、claims に入れていない。
- arXiv 2511.00872 は本文の数字を確認できておらず、引用は題名しか使えなかった。2506.17208 の単一対複数の結論も、ページ本文では確認できなかった。
- 実務家（practitioners）の分野は、Cognition の記事 1 件のみ。Simon Willison などの個人の記事は、検索結果に出ただけで本文を読んでいない。
- 調べ方の制約として、WebFetch は小さなモデルで要約した結果を返すので、一部の引用は要約が写したものだ。機械照合で外れる可能性がある。

## 検索語

- Cursor scaling long-running autonomous coding agents planners workers blog
- survey LLM-based agents software engineering multi-agent vs single agent SWE-bench arXiv
- OpenAI Codex cloud agent long-running tasks Codex SDK GitHub Action exec non-interactive docs
- GitHub Copilot coding agent assigns issue creates pull request runs in GitHub Actions docs
- OpenAI harness engineering Codex agent-generated codebase zero manually written code
- MetaGPT ChatDev role-based multi-agent software development vs single agent critique ICLR

## 問い

# 自律的なソフトウェア開発の全体の構成：役の分け方と、長く走るエージェントの回し方

## 段
map

## 決めたい判断
自律開発環境（計画・実装・検証・統合を、人の承認を最小にして回す仕組み）の上位の設計を、どの系統に寄せるか。いまの独自の構成（計画役・実装役・総監督・統合 PR を回す常駐のループ）を残すか、既製の道具の上に作り直すか

## 判断を変えうるもの
最前線の企業が、自社の道具（エージェントの SDK・ヘッドレスの CLI・クラウドで走るエージェント・CI の統合）で、同じことを標準の形で提供しているなら、独自の構成を保つ理由が薄れる。逆に、長く走る自律開発を標準の形で回している例が無いなら、独自の構成に理由がある

## 分野
frontier_labs
practitioners_at_scale
academia
standards_tools
practitioners

## 問い
AI のコーディング エージェントに、長い時間（数時間〜数日）・多くのタスクを、人の介入を少なくして回させる仕組みが、いまどう作られているかを、手法の系統ごとに集めてください。

- 役の分け方（計画・実装・レビュー・統合を別のエージェントにするか、1 つのエージェントに任せるか、オーケストレーターと下位のエージェントの形か）
- 長く走るエージェントの回し方（タスクの待ち行列、状態の持ち方、文脈の引き継ぎ、失敗からの再開）
- エージェントの提供元が公式に出している道具（SDK・ヘッドレス実行・バックグラウンドのエージェント・GitHub との統合）と、その想定する使い方
- 研究（自律的なソフトウェア工学のエージェントの構成の比較・サーベイ）
