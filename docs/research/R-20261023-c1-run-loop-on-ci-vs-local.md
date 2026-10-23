# 自律開発の内側のループを、どこで回すか：CI の上の公式のエージェントか、手元の PC の独自のループか

- 記録：R-20261023-c1-run-loop-on-ci-vs-local
- 段：compare
- 決めたい判断：エージェントにタスクを実装させて PR を出す内側のループを、手元の Windows の PC の独自の常駐ループ（いまの形）で回し続けるか、GitHub Actions などの CI の使い捨て環境の上で、提供元の公式のエージェント（Claude Code GitHub Actions・Codex・Gemini CLI の Actions・Copilot の coding agent）に置き換えるか
- 調べた日時：2026-10-23T14:12:08+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 6・quote_not_found 8・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | claude-code-action は、API キー（ANTHROPIC_API_KEY）と、Claude の定額契約（Pro/Max/Team/Enterprise）の OAuth トークン（CLAUDE_CODE_OAUTH_TOKEN）の両方で認証できる。トークンは手元で claude setup-token を実行して作る。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | quote_not_found | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C2 | claude-code-action の 1 回の実行は、Actions の分と API トークンの 2 種類の資源を使う。定額の OAuth トークンなら API 課金ではなく定額の契約の枠を使う。上限は --max-turns、ワークフローのタイムアウト、GitHub の concurrency 制御で自分で掛ける。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | verified | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C3 | 公式文書は、複数のリポジトリで共有する秘密には、OAuth トークンではなく Console の API キーを勧めている。OAuth トークンは setup-token を実行した個人の契約に結び付くため。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | quote_not_found | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C4 | claude setup-token が作るトークンは 1 年有効で、モデルへの要求だけができる。Remote Control の接続や claude.ai コネクタの取得はできない。--bare を付けるとこのトークンは読まれない。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | quote_not_found | [Authentication - Claude Code Docs](https://code.claude.com/docs/en/authentication) |
| C5 | 認証の優先順位では ANTHROPIC_API_KEY が CLAUDE_CODE_OAUTH_TOKEN より上位にある。非対話（-p）では、キーがあれば必ずそれが使われる。定額の枠で回したいのに API キーの環境変数が残っていると、API 課金になる、または認証に失敗する。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | quote_not_found | [Authentication - Claude Code Docs](https://code.claude.com/docs/en/authentication) |
| C6 | claude-code-action の issue #1281（未解決）で、Max 契約の OAuth トークンを使うと認証に失敗するという報告がある。Pro から Max への切り替えの後に起きたとされ、別の利用者は最初から Max でも再現し、ログに何も出ないと書いている。API キーだけが回避策で、その場合は従量課金に移る。 | B: Claude Code Action (CI上) | practitioners | secondary | fact | 2026-08-19 | quote_not_found | [claude-code-action issue #1281](https://github.com/anthropics/claude-code-action/issues/1281) |
| C7 | GitHub が用意する実行時間の上限は、GitHub が運用する runner ではジョブ 1 件あたり最大 6 時間。自前の runner では最大 5 日。並行ジョブの数は契約により、標準 runner で Free 20、Pro 40、Team 60、Enterprise 500。 | B/C: GitHub Actions の上限 | standards_tools | primary | fact | 不明 | verified | [Actions limits - GitHub Docs](https://docs.github.com/en/actions/reference/limits) |
| C8 | claude-code-action の既定では、Claude の GitHub App 権限で push するので他の CI が動く。ワークフローに既定の GITHUB_TOKEN を渡すと、Claude の commit では CI が起動しない。必須チェックで合否を決める構成では、この点が合否の経路に関わる。 | B: Claude Code Action (CI上) | frontier_labs | primary | fact | 不明 | quote_not_found | [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions) |
| C9 | OpenAI Codex は、プログラム的な利用（CI/CD）には API キー認証を勧めている。API キーでは標準の API 料金で課金され、ChatGPT 契約に含まれる枠は使われない。Codex cloud は ChatGPT でのサインインが必要。 | C: Codex / Gemini の Actions | frontier_labs | primary | fact | 不明 | verified | [Codex Authentication（旧 developers.openai.com/codex/auth）](https://learn.chatgpt.com/docs/auth) |
| C10 | openai/codex-action は提供元ごとの API キーを必須とする。GitHub が運用する Windows runner には対応した sandbox が無い。API キーは proxy を通るため、read-only でも Codex が読める可能性がある。 | C: Codex / Gemini の Actions | frontier_labs | primary | fact | 不明 | verified | [openai/codex-action README](https://github.com/openai/codex-action) |
| C11 | Gemini CLI の Actions（run-gemini-cli）は Gemini API キー、Workload Identity Federation などで認証する。README に具体的な枠や料金は書かれておらず、「無料の枠」とだけある。DEBUG 環境変数を設定すると CLI が止まる。 | C: Codex / Gemini の Actions | frontier_labs | primary | fact | 不明 | quote_not_found | [google-github-actions/run-gemini-cli](https://github.com/google-github-actions/run-gemini-cli) |
| C12 | Copilot の coding agent は 1 セッションの実行が最大 59 分で、延長できない。1 度に 1 つのブランチで作業し、1 つのタスクにつき PR は 1 つ。 | D: Copilot coding agent | frontier_labs | primary | fact | 不明 | verified | [About GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/about-coding-agent) |
| C13 | Copilot の coding agent は 1 セッションにつきプレミアム要求を 1 回消費する。作業の大きさにかかわらず同じ。加えて GitHub Actions の分を使い、その量は作業の長さで変わる。 | D: Copilot coding agent | frontier_labs | primary | fact | 2025-07-10 | verified | [GitHub Copilot coding agent now uses one premium request per session](https://github.blog/changelog/2025-07-10-github-copilot-coding-agent-now-uses-one-premium-request-per-session/) |
| C14 | 自律エージェントに本物の PR を出させた個人の事例に、変更範囲の逸脱と force-push 寸前の失敗があった。対策はブランチ名の規則・commit の規則・レビューの関門・触れる範囲の上限。単一の個人の記事で、規模の大きい運用ではない。 | A/B: 自律PRの運用 | practitioners | secondary | opinion | 不明 | quote_not_found | [5 lessons from letting my AI coding agent open its own pull requests](https://dev.to/yureki_lab/5-lessons-from-letting-my-ai-coding-agent-open-its-own-pull-requests-7g) |

## 抜粋

- C1：「`CLAUDE_CODE_OAUTH_TOKEN`: an OAuth token that authenticates with your Claude subscription, available on Pro, Max, Team, and Enterprise plans. Generate one by running `claude setup-token` locally.」
- C2：「If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.」
- C3：「an OAuth token is tied to the subscription of the person who ran `claude setup-token`.」
- C4：「This token authenticates with your Claude subscription and requires a Pro, Max, Team, or Enterprise plan. It can only make model requests, so it can't establish [Remote Control](/docs/en/remote-control.md) sessions or fetch [claude.ai connectors](/docs/en/mcp.md#use-mcp-servers-from-claude-ai).」
- C5：「In non-interactive mode (`-p`), the key is always used when present.」
- C6：「Only `ANTHROPIC_API_KEY` works as a workaround, but that defeats the purpose of OAuth for subscription users.」
- C7：「Each job in a workflow can run for up to 6 hours of execution time.」
- C8：「GitHub doesn't trigger workflows on commits made with the default `GITHUB_TOKEN`.」
- C9：「Use API key authentication for programmatic Codex CLI workflows, such as CI/CD jobs.」
- C10：「GitHub-hosted Windows runners lack a supported sandbox.」
- C11：「Do not use the `DEBUG` environment variable as it causes the Gemini CLI to hang waiting for node debugger to attach.」
- C12：「Each Copilot cloud agent session has a maximum execution time of 59 minutes. This limit cannot be extended or bypassed.」
- C13：「The entire session—regardless of how many files are modified or how complex the task is—uses one premium request.」
- C14：「不明（検索結果の要約のみで、本文を直接確かめていない。このため quote は空）」

## 確かめられなかったこと

- 最後の claim（dev.to の個人の記事）は本文を直接開いて確かめておらず、quote が本文の抜粋になっていない。機械の照合では必ず落ちるので、claims から外して扱うこと。
- claude-code-action の実行時間の上限、状態（どのタスクが済んだか）の引き継ぎ、失敗からの再開、別の実行をまたぐ session の再開について、公式の文書に記述を見つけられなかった。文書にあるのは --max-turns・ワークフローのタイムアウト・concurrency 制御で自分で上限を掛けるという助言まで。
- Copilot の coding agent の並行セッション数の上限は、公式の文書で見つけられなかった。
- Gemini CLI の Actions と Codex の Actions の、実行時間・並行数・状態の引き継ぎの記述は確かめていない。Gemini の枠の数字も README に無かった。
- エージェントを大規模に、または長く CI の上で運用している当事者の事後検証（失敗の件数、費用の実測など）は、見つけられなかった。見つかったのは個人のブログと、仕様の文書だけ。
- 手元で独自のループを回す側の欠点（PC の電源や接続、認証の期限切れなど）を、一次の出典で集めていない。Claude Code の認証の文書に、無人で動かすセッションがログインの期限切れで進まなくなるという記述はある（Renew an expiring login の節）が、claim にしていない。
- 定額の契約の枠を CI で使うことが、規約の上で許されるかどうか（利用枠の制限や規約）は確かめていない。文書上は OAuth トークンの使用が案内されている、という点までしか言えない。
- 公開日は多くの出典で取れなかった（「不明」）。issue #1281 の日付は、本文に書かれた投稿の日付（2026-08-19）。
- WebFetch は小さなモデルを通した要約で返るため、quote のうち github.com/openai/codex-action と Gemini の README 由来のものは、本文と一字一句同じか確証が無い（confidence を medium にした）。

## 検索語

- claude-code-action CLAUDE_CODE_OAUTH_TOKEN Max subscription GitHub Actions setup-token
- openai codex-action GitHub Actions authentication API key ChatGPT plan
- Copilot coding agent premium requests Actions minutes billing per session
- autonomous coding agent GitHub Actions lessons learned failures cost postmortem long-running
- claude-code-action GitHub Actions timeout max turns session resume across runs state limitations FAQ
- Copilot coding agent limitations concurrent sessions session logs continue pull request review comments iterate

## 問い

# 自律開発の内側のループを、どこで回すか：CI の上の公式のエージェントか、手元の PC の独自のループか

## 段
compare

## 決めたい判断
エージェントにタスクを実装させて PR を出す内側のループを、手元の Windows の PC の独自の常駐ループ（いまの形）で回し続けるか、GitHub Actions などの CI の使い捨て環境の上で、提供元の公式のエージェント（Claude Code GitHub Actions・Codex・Gemini CLI の Actions・Copilot の coding agent）に置き換えるか

## 判断を変えうるもの
CI の上の公式のエージェントで、長く多くのタスクを回している運用の例と、その失敗例・費用（API の課金か定額の契約か、Actions の時間）・制約（実行時間の上限・状態の引き継ぎ・並行数）。手元の独自のループを保つべき理由（定額の契約の利用枠を使える、など）があるか

## 候補
A. 手元の PC の独自の常駐ループ（計画役・実装役・Gate 1・統合 PR を自前で回す）
B. GitHub Actions の上で、Claude Code GitHub Actions（anthropics/claude-code-action）を、台帳のタスク（issue）ごとに動かし、合否は必須チェックで決める
C. Gemini CLI の GitHub Actions（google-github-actions/run-gemini-cli）や OpenAI Codex の Actions を同じ形で使う
D. Copilot の coding agent など、提供元が運用するクラウドのエージェントに issue を割り当てる

## 問い
上の候補ごとに、次のことを集めてください。

- 公式の文書にある使い方・認証の方式（API キーか、定額の契約のトークンか）・費用の仕組み・実行時間や並行数の上限
- 大規模に、または長く運用している例と、その失敗例・事後検証
- 状態（どのタスクが済んだか）の引き継ぎ方と、失敗からの再開
- 反対意見（CI の上で回すことの欠点、手元で回すことの欠点）
