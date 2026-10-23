# エージェントを無人で走らせる実行環境：隔離・権限・資格情報

- 記録：R-20261023-m4-agent-execution-isolation
- 段：map
- 決めたい判断：無人で走る実装役と、それを回す常駐のループを、どの実行環境で動かすか（いまの PC の OS アカウントの分離・WSL2・VM・コンテナ・クラウドで走るエージェント・CI のランナー）
- 調べた日時：2026-10-23T14:07:57+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 12・quote_not_found 4・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic の組み込みサンドボックス（Bash ツール）は、OS の仕組みでファイルとネットワークを制限する。ホスト上で動き、コンテナは使わない。ネイティブ Windows では対応せず、Windows では WSL2 を使う。 | OS 組み込みのサンドボックス（コマンド単位） | frontier_labs | primary | fact | 不明 | verified | [Choose a sandbox environment (Claude Code docs)](https://code.claude.com/docs/en/sandbox-environments) |
| C2 | Claude Code のサンドボックスは macOS・Linux・WSL2 で動く。ネイティブ Windows ではコマンドが隔離されずに実行される。 | OS 組み込みのサンドボックス（コマンド単位） | frontier_labs | primary | fact | 不明 | verified | [Configure the sandboxed Bash tool (Claude Code docs)](https://code.claude.com/docs/en/sandboxing) |
| C3 | Anthropic は、完全に無人で動かすなら Bash サンドボックス単体では足りないと明記している。コンテナ・VM・サンドボックス ランタイムの内側で動かし、`--dangerously-skip-permissions` もそこで使うよう勧めている。 | OS 組み込みのサンドボックス（コマンド単位） | frontier_labs | primary | fact | 不明 | quote_not_found | [Choose a sandbox environment (Claude Code docs)](https://code.claude.com/docs/en/sandbox-environments) |
| C4 | Anthropic の比較表では、隔離の強さは Bash サンドボックス、サンドボックス ランタイム、dev container、カスタム コンテナ、VM、クラウド セッションの順に広がる。VM は独自のカーネルを持ち、最も強い分離とされている。 | VM・microVM | frontier_labs | primary | fact | 不明 | verified | [Choose a sandbox environment (Claude Code docs)](https://code.claude.com/docs/en/sandbox-environments) |
| C5 | dev container（Docker）は、既定拒否のファイアウォールと組み合わせると、`--dangerously-skip-permissions` を使った無人運用を支える。ただし公式文書は、悪意あるプロジェクトによる持ち出しを防げないと警告している。コンテナ内の Claude 資格情報（~/.claude）も持ち出せる。ホストの秘密情報はマウントせず、リポジトリ単位か短命のトークンを使うよう勧めている。 | コンテナ（dev container・ファイアウォール付き） | frontier_labs | primary | fact | 不明 | quote_not_found | [Development containers (Claude Code docs)](https://code.claude.com/docs/en/devcontainer) |
| C6 | サンドボックスは、ファイルの隔離とネットワークの隔離の両方がそろって初めて有効だとされる。Anthropic の内部利用では、許可の確認が 84% 減った。 | OS 組み込みのサンドボックス（コマンド単位） | frontier_labs | primary | fact | 不明 | verified | [Beyond permission prompts: making Claude Code more secure and autonomous](https://www.anthropic.com/engineering/claude-code-sandboxing) |
| C7 | Anthropic のクラウド セッションは、Anthropic 管理の隔離された VM で動く。git の資格情報と署名鍵は VM の外に置き、プロキシが範囲を絞った資格情報で代理認証する。ネットワークは既定で制限される。ネットワークを無効にしても Anthropic API とは通信できるため、データが VM の外へ出うる。 | 提供元が運用するクラウド VM と資格情報プロキシ | frontier_labs | primary | fact | 不明 | verified | [Use Claude Code in the cloud (Claude Code docs)](https://code.claude.com/docs/en/claude-code-on-the-web) |
| C8 | OpenAI の Codex クラウド環境は、エージェント段階のインターネットを既定で遮断する。有効にすると、プロンプト インジェクションと、コードや秘密情報の持ち出しの危険が増すと公式文書が認めている。許可リストは『なし』と『依存関係の一般的な通信先』などから選べる。 | 提供元が運用するクラウド VM と資格情報プロキシ | frontier_labs | primary | fact | 不明 | verified | [Agent internet access (Codex cloud docs)](https://learn.chatgpt.com/docs/cloud/internet-access) |
| C9 | Codex クラウドでは、秘密情報はセットアップ スクリプトの間だけ使え、エージェント段階が始まる前に取り除かれる。 | 段階分離（準備段階だけ秘密情報とネットワーク） | frontier_labs | primary | fact | 不明 | verified | [Cloud environments (Codex docs)](https://learn.chatgpt.com/docs/environments/cloud-environment) |
| C10 | GitHub Copilot のクラウド エージェントは、使い捨てのクラウド環境で動く。リポジトリ内の文脈だけを見られる。push は 1 つのブランチに限られる。ワークフローは、人がレビューして承認するまで既定では起動しない。 | CI ランナー型（Actions 上の使い捨て環境と書き込み範囲の制限） | frontier_labs | primary | fact | 不明 | verified | [Risks and mitigations for GitHub Copilot cloud agent](https://docs.github.com/en/copilot/concepts/agents/coding-agent/risks-and-mitigations) |
| C11 | Ramp は、エージェントの各セッションを Modal 上の隔離された VM で動かしている。GitHub App のトークンを clone ごとに新しく発行し、PR は起票者本人のトークンで作る。そのため、起票者が自分の変更を承認できない。 | セッションごとの使い捨てクラウド サンドボックス（VM） | practitioners_at_scale | primary | fact | 不明 | quote_not_found | [Why we built our own background agent (Ramp Builders)](https://builders.ramp.com/post/why-we-built-our-background-agent) |
| C12 | GitHub Actions の公式の強化ガイドは、GITHUB_TOKEN に必要最小限の権限だけを与えるよう求めている。公開リポジトリでの自前ランナーは、誰でも PR で環境を侵害できるので危険だとする。just-in-time の使い捨てランナーは 1 ジョブで消える。ハードウェアを再利用すると情報が漏れうるため、毎回クリーンな環境を作るよう求めている。 | CI ランナー型（Actions 上の使い捨て環境と書き込み範囲の制限） | standards_tools | primary | fact | 不明 | verified | [Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions) |
| C13 | Anthropic の sandbox-runtime は、プロセス全体を Seatbelt か bubblewrap で包む。既定ではネットワークを拒否する。.git/hooks や .mcp.json などの書き込みも自動で拒否する。ただし Linux と WSL2 では、拒否リストを起動時に 1 回だけ作る。そのため、セッション中に作った `git clone` などは対象外になる。まだベータの研究プレビューである。 | プロセス全体のサンドボックス ランタイム（bubblewrap・Seatbelt） | standards_tools | primary | fact | 不明 | verified | [Choose a sandbox environment (Claude Code docs)](https://code.claude.com/docs/en/sandbox-environments) |
| C14 | Docker Sandboxes は microVM を使う。専用の Docker デーモンとワークスペース同期を持ち、Docker Desktop が無くても動く。Anthropic の文書は、VM や microVM（Firecracker など）を、信頼できないコードの評価に向く選択肢として挙げている。 | VM・microVM | standards_tools | primary | fact | 不明 | quote_not_found | [Choose a sandbox environment (Claude Code docs)](https://code.claude.com/docs/en/sandbox-environments) |
| C15 | AIShellJack は、GitHub Copilot と Cursor に対するプロンプト インジェクションを大規模に測った。悪意あるコマンドの実行に成功した割合は、最大で 84% に達した。 | 脅威の実測（隔離が必要な根拠） | academia | primary | fact | 2025-09-26 | verified | ["Your AI, My Shell": Demystifying Prompt Injection Attacks on Agentic AI Coding Editors](https://arxiv.org/abs/2509.22040) |
| C16 | Simon Willison は、「致命的な三つ組」を挙げた。私的データへのアクセス、信頼できない内容への接触、データを盗める外部通信の 3 つがそろうと、エージェントは危険になる。実務家の枠組みとして、隔離の設計でどの足を切るかの根拠になる。 | 設計原則（三つ組のうち一つを断つ） | practitioners | secondary | opinion | 2025-06-16 | verified | [The lethal trifecta for AI agents](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/) |

## 抜粋

- C1：「This option does not support native Windows. On Windows hosts, use WSL2 or one of the container or VM approaches below.」
- C2：「The sandbox runs on macOS, Linux, and WSL2. On native Windows, Claude Code runs commands unsandboxed.」
- C3：「The [sandboxed Bash tool](#sandboxed-bash-tool) on its own constrains only shell commands, so it is not sufficient for fully unattended runs in either mode.」
- C4：「A dedicated virtual machine provides the strongest separation, with its own kernel and, in cloud or microVM deployments, its own virtualized hardware.」
- C5：「When executed with `--dangerously-skip-permissions`, dev containers do not prevent a malicious project from exfiltrating anything accessible inside the container, including the Claude Code credentials stored in [`~/.claude`](/docs/en/claude-directory).」
- C6：「In our internal usage, we've found that sandboxing safely reduces permission prompts by 84%.」
- C7：「in Anthropic-hosted environments, git credentials and signing keys stay outside the sandbox, and a proxy authenticates on the session's behalf with scoped credentials.」
- C8：「By default, Codex blocks internet access during the agent phase.」
- C9：「For security reasons, secrets are removed before the agent phase starts.」
- C10：「Copilot cloud agent only has the ability to push to a single branch.」
- C11：「Each session runs in a sandboxed VM on Modal with everything an engineer would have locally」
- C12：「Re-using hardware to host JIT runners can risk exposing information from the environment.」
- C13：「On Linux and WSL2, the runtime builds the deny list once at launch.」
- C14：「[Docker Sandboxes](https://docs.docker.com/ai/sandboxes/) provides a microVM with its own Docker daemon and workspace sync, which can run Claude Code on any host with Docker Sandboxes installed.」
- C15：「attack success rates can reach as high as 84% for executing malicious commands」
- C16：「The ability to externally communicate in a way that could be used to steal your data」

## 確かめられなかったこと

- 迂回された例（Claude Code のサンドボックス脱出）は、検索結果の要約でしか確認できていない。対象は CVE-2026-39861（symlink）、CVE-2025-66479（allowedDomains が空だと全許可になる）、SOCKS5 の null バイト迂回（v2.1.90 で修正）、CVE-2026-25725（存在しない settings.json に hook を注入できた）。GitHub の公式アドバイザリ本文から一字一句の引用を取れていないため、claims には入れなかった。出典の多くは二次のブログである。Anthropic 自身の事後検証は見つからなかった。
- Replit のエージェントが本番 DB を削除した件（2025-07）は、報道と二次のブログしか見つからなかった。Replit の事後検証の文書は見つからなかった。Lemkin の投稿を主な根拠とする報道が中心である。
- Stripe・Cloudflare・Shopify・Cognition など、Ramp 以外の大規模運用企業の一次の技術ブログは見つからなかった。Cognition（Devin）は ZenML による要約しか得られなかった。Ramp の記事は fetch の要約結果から引用したため、quote の一字一句は再確認を要する。
- academia：サーベイ論文の本文は確認できていない。arXiv 2601.17548（SoK）と 2605.25871 は、検索の要約でしか見ていない。『18 の防御のどれも、適応型攻撃の半分以上を防げない』という記述は二次のブログのもので、未検証である。コンテナ、gVisor、microVM を比べた査読済みの測定は見つからなかった。見つかったのは売り手や実務家のブログだけだった。
- GitHub Copilot cloud agent の『インターネット制限（ファイアウォール）』と『資格情報の制限』の一次文書は、fetch の要約でしか見ていない。文書の旧称は coding agent で、現在は cloud agent と書かれている。引用文は要約から写したため、再確認を要する。
- 1 台の Windows 11 Home での実行環境について、一次の文書は見つからなかった。得られたのは、native Windows ではサンドボックスが効かず WSL2 か VM かコンテナを使うという Anthropic の記述だけである。Windows 11 Home での Hyper-V、WSL2、別の OS アカウントによる分離の強さを測った出典はない。
- 公開日は多くの文書ページで確認できず、『不明』とした。新旧の判定は URL と内容（2026 年の CVE への言及など）に頼っている。
- Anthropic の github.com/anthropics/claude-code の devcontainer や init-firewall.sh、sandbox-runtime の README の本文は、直接は読んでいない。

## 検索語

- OpenAI Codex cloud agent internet access disabled by default sandbox setup script secrets removed
- Claude Code sandbox escape bypass vulnerability CVE postmortem coding agent
- arXiv sandboxing LLM coding agents isolation container microVM gVisor survey agent security
- arXiv AI coding agent security prompt injection attack success rate coding assistants Cursor Copilot Claude Code
- engineering blog running background coding agents at scale sandboxed VMs Ramp OR Stripe OR Cloudflare OR Shopify unattended agents
- Replit agent deleted production database postmortem July 2025 Jason Lemkin
- Anthropic claude-code-sandboxing 記事の取得
- Claude Code devcontainer 文書の取得
- GitHub Copilot cloud agent about / risks-and-mitigations の取得
- Codex cloud environments / internet access の取得
- GitHub Actions security hardening の取得
- Claude Code sandboxing / sandbox-environments / claude-code-on-the-web の取得
- arxiv.org/abs/2509.22040 の取得
- builders.ramp.com の取得
- simonwillison.net lethal-trifecta の取得

## 問い

# エージェントを無人で走らせる実行環境：隔離・権限・資格情報

## 段
map

## 決めたい判断
無人で走る実装役と、それを回す常駐のループを、どの実行環境で動かすか（いまの PC の OS アカウントの分離・WSL2・VM・コンテナ・クラウドで走るエージェント・CI のランナー）

## 判断を変えうるもの
提供元や大規模に運用している企業が、無人のエージェントをどこで走らせているか。それぞれの方法で、ファイル・ネットワーク・資格情報をどこまで閉じ込められるか。1 台の Windows の PC で回すことの制約

## 分野
frontier_labs
practitioners_at_scale
academia
standards_tools
practitioners

## 問い
AI のコーディング エージェントを、人が見ていないところで長く走らせるときの実行環境と隔離の手法の系統を集めてください。

- 提供元が勧める実行環境（サンドボックス・コンテナ・VM・クラウドのエージェント・CI の中での実行）
- 権限と資格情報の扱い（トークンの範囲・専用のアカウント・ネットワークの制限・書き込みの範囲）
- 迂回された例や事故の事後検証
