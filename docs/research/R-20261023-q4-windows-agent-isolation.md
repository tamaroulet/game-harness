# Windows で、AI のエージェントの CLI を別の権限で閉じ込める方法の、いまの実践と限界

- 記録：R-20261023-q4-windows-agent-isolation
- 調べた日時：2026-10-23T12:20:36+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 10・quote_not_found 2・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|
| C1 | Claude Code のサンドボックスは macOS・Linux・WSL2 で動き、ネイティブ Windows では動かない（ネイティブ Windows ではコマンドがサンドボックス外で走る） | fact | 不明 | verified | [Configure the sandboxed Bash tool - Claude Code Docs](https://code.claude.com/docs/en/sandboxing) |
| C2 | サンドボックス付きの Bash ツールだけでは、無人実行を完全に任せるには足りず、コンテナや VM の中で動かすことが勧められている | fact | 不明 | verified | [Choose a sandbox environment - Claude Code Docs](https://code.claude.com/docs/en/sandbox-environments) |
| C3 | サンドボックスを起動できない（依存が無い、またはプラットフォームが未対応の）場合、既定ではコマンドがサンドボックス無しで実行される。failIfUnavailable を true にすると起動時に失敗させられる | fact | 不明 | verified | [Configure the sandboxed Bash tool - Claude Code Docs](https://code.claude.com/docs/en/sandboxing) |
| C4 | Windows Sandbox ではネットワークが既定で有効で、信頼できないアプリが内部ネットワークに届きうる。無効にするにはカスタムの .wsb ファイルを使う | fact | 2026-03-29 | verified | [Use and configure Windows Sandbox - Microsoft Learn](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file) |
| C5 | Windows Sandbox で書き込み可能に共有したホストのフォルダへの変更は、サンドボックスを破棄した後も残る | fact | 2026-03-29 | verified | [Use and configure Windows Sandbox - Microsoft Learn](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file) |
| C6 | Windows Sandbox の Protected Client モードは、AppContainer Isolation の中でサンドボックスを動かし、資格情報・デバイス・ファイル・ネットワーク・プロセス・ウィンドウを隔離する（ただし、ファイルのコピー＆ペーストが制限されることがある） | fact | 2026-03-29 | verified | [Use and configure Windows Sandbox - Microsoft Learn](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file) |
| C7 | Docker Desktop の Windows コンテナは OS の機能で動き、Windows ホスト上で直接走る。Linux コンテナは Docker が管理する最小の Linux VM の中で動く | fact | 不明 | verified | [Understand permission requirements for Windows - Docker Docs](https://docs.docker.com/desktop/windows/permission-requirements/) |
| C8 | Hyper-V 隔離のコンテナは、高度に最適化された VM の中で動き、事実上それぞれ独自のカーネルを持つ。プロセス隔離のコンテナはホストとカーネルを共有する | fact | 2025-01-23 | verified | [Isolation modes - Microsoft Learn](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container) |
| C9 | Windows 10 Pro や Enterprise でのプロセス隔離は、開発とテスト用に位置づけられている（運用は Windows Server を使うよう勧められている） | fact | 2025-01-23 | verified | [Isolation modes - Microsoft Learn](https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container) |
| C10 | ある第三者の解説は、Codex CLI が AppContainer を採らなかった理由として、ファイル読み取りの既定拒否がコーディング用エージェントには非現実的だったことを挙げている | opinion | 2026-07-18 | verified | [Codex CLI Windows sandbox architecture (Daniel Vaughan blog)](https://codex.danielvaughan.com/2026/07/18/codex-cli-windows-sandbox-architecture-powershell-ast-safety-elevated-unelevated-appcontainer-restricted-tokens/) |
| C11 | 制限付きトークンは、誰でも書き込める（Everyone に書き込みを許す）ディレクトリでは書き込みを防げない（この解説は非昇格モードでの限界として挙げている） | opinion | 2026-07-18 | quote_not_found | [Codex CLI Windows sandbox architecture (Daniel Vaughan blog)](https://codex.danielvaughan.com/2026/07/18/codex-cli-windows-sandbox-architecture-powershell-ast-safety-elevated-unelevated-appcontainer-restricted-tokens/) |
| C12 | Codex CLI の Windows の名前付きパイプは、Everyone に完全な制御を与える記述子で作られており、別ユーザーの資格情報（更新トークンを含む）が盗まれる脆弱性があった（CVSS v3.1 8.5） | vendor_claim | 不明 | quote_not_found | [Cymulate - OpenAI Codex CLI named pipe vulnerability](https://cymulate.com/blog/openai-codex-cli-named-pipe-vulnerability/) |

## 抜粋

- C1：「The sandbox runs on macOS, Linux, and WSL2. On native Windows, Claude Code runs commands unsandboxed.」
- C2：「so it is not sufficient for fully unattended runs in either mode.」
- C3：「By default, if the sandbox can't start because a dependency is missing or the platform is unsupported, Claude Code runs commands without sandboxing.」
- C4：「Networking is enabled by default. This can expose untrusted applications to the internal network.」
- C5：「Changes made during a Sandbox session to a mapped folder with write-permissions will persist after a Sandbox is disposed.」
- C6：「Sandbox adds a new layer of security boundary by running inside an」
- C7：「Windows containers are implemented using operating system features, and run directly on the Windows host.」
- C8：「each container runs inside of a highly optimized virtual machine and effectively gets its own kernel」
- C9：「Running with process isolation on Windows 10 Pro and Enterprise is meant for development/testing.」
- C10：「AppContainer’s default-deny posture for filesystem reads made it impractical」
- C11：「the restricted token cannot prevent writes there.」
- C12：「We found that those pipes were created with a security descriptor that handed full control to Everyone.」

## 確かめられなかったこと

- Windows Sandbox の公式な制限ページは検索で見つからず、設定ページ（configure）から確かめた。Windows Sandbox の既知の脱出例は確かめられなかった
- AppContainer・制限付きトークン・Win32 App Isolation の一次資料（Microsoft Learn の API 文書）は取得できず、確認できたのは第三者の解説のみ
- Hyper-V の隔離ページは Windows Server 向けに書かれている。Windows 11 Home（Docker Desktop の WSL2 バックエンドを含む）での隔離の強さについての一次資料は見つからなかった
- Docker Desktop で WSL2 バックエンドを使うときの隔離の範囲（資格情報、ホストのファイル共有、ネットワーク）を確かめる一次資料は見つからなかった
- 専用のローカルユーザーと NTFS の ACL で閉じ込める方法について、一次資料または実践の報告は見つからなかった
- WSL2 の中で Claude Code を閉じ込めたときの迂回例（Windows 実行ファイルの呼び出し、/mnt/c の利用、資格情報の読み取り）は、一次資料の本文で確かめられなかった
- Anthropic 以外（Google、OpenAI）の公式な閉じ込めの勧めは調べていない。OpenAI の Codex の Windows サンドボックスの公式記事は検索結果に出たが、本文を取得していない
- Cymulate の『設定を使ったサンドボックス脱出』の主張（ホスト側の設定を書き換えて次回起動時に実行させる）と、Accomplish による Codex の二件の脱出は、一次資料の本文を確かめていない
- BeyondTrust の Copilot Studio の事例は、検索結果の要約のみで本文を確かめていない（対象は Copilot Studio のコードインタプリタであり、CLI ではない）
- Cymulate の脆弱性記事は『Last Updated: July 5, 2026』と表示されているだけで、公開日は確かめられなかった。CVSS と PR 番号は本文の要約からの写しで、原文との完全な一致は確かめていない
- Claude Code のサンドボックス文書の『Windows binaries are blocked』『WSL1 では動かない』などの記述は、検索結果の要約では確かめたが、本文の一字一句の抜粋としては取っていない

## 問い

# Windows で、AI のエージェントの CLI を別の権限で閉じ込める方法の、いまの実践と限界

Windows 11 の上で、コーディング エージェントの CLI（Node.js などで動く対話型のコマンド）を、利用者の本来の権限から切り離して動かす方法の、いまの実践と限界を集めてください。

- 専用のローカル ユーザーと NTFS の ACL、AppContainer、Windows Sandbox、Hyper-V の VM、WSL2、Docker（Windows コンテナ・Linux コンテナ）を比べてください
- それぞれで、ファイルの書き込み・ネットワーク・資格情報（トークン）をどこまで閉じ込められるか、迂回された例や既知の限界があれば含めてください
- エージェントの提供元（Anthropic・Google・OpenAI など）が公式に勧める閉じ込め方があれば含めてください
