# Unity への接続の下調べ（公式の MCP・ライセンス・閉じ込め）

- 記録：R-20261117-unity-connection-g06
- 段：compare
- 決めたい判断：Unity の実行を設計 §9 の ①（Windows の Unity をアダプタがクローンに対して呼ぶ）と ②（Linux の Unity をコンテナで動かす）のどちらにするか。Editor を操る口（公式の MCP）を使うか
- 調べた日時：2026-11-17T16:49:27+09:00
- 調べ役：seat（）
- 照合：verified 10・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Unity は、Editor の中の AI Assistant パッケージの MCP サーバーを非推奨にした。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Unity CLI as the replacement for the in-Editor MCP server](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli) |
| C2 | Editor の中の MCP サーバーの代わりは、Unity CLI の unity mcp の命令。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Unity CLI as the replacement for the in-Editor MCP server](https://docs.unity.com/en-us/unity-cli/replace-mcp-server-unity-cli) |
| C3 | AI Assistant パッケージ（2.20）の MCP サーバーの文書の冒頭に、非推奨と CLI への移行が書かれている。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Get started with MCP server (Assistant 2.20.0-pre.2)](https://docs.unity3d.com/Packages/com.unity.ai.assistant@2.20/manual/integration/unity-mcp-get-started.html) |
| C4 | Editor の中の MCP サーバーの橋渡しは、Editor が読み込まれたときに始まる（Editor を開いている間だけ使える）。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Get started with MCP server (Assistant 2.20.0-pre.2)](https://docs.unity3d.com/Packages/com.unity.ai.assistant@2.20/manual/integration/unity-mcp-get-started.html) |
| C5 | Unity CLI の 1.0.1 は 2026-11-16 に出た。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Unity CLI release notes for November 2026](https://docs.unity.com/en-us/unity-cli/release-notes/2026/november) |
| C6 | Unity CLI が Editor を操るときは、Unity Pipeline パッケージが、ローカルの HTTP の API で Editor を操り、ビルド・テスト・開発の流れを自動化する。 | Editor を外から操る口（公式の MCP と CLI） | standards_tools | primary | fact |  | verified | [Unity CLI](https://docs.unity.com/en-us/unity-cli) |
| C7 | Unity Personal のライセンスを有効にする・返す方法は、Unity Hub だけ。 | Unity のライセンスとバッチモード | standards_tools | primary | fact |  | verified | [License activation methods (Unity 6000.0)](https://docs.unity3d.com/6000.0/Documentation/Manual/LicenseActivationMethods.html) |
| C8 | 手動の有効化（ライセンスの要求のファイル）は、Personal のプランのライセンスには使えない。 | Unity のライセンスとバッチモード | standards_tools | primary | fact |  | verified | [License activation methods (Unity 6000.0)](https://docs.unity3d.com/6000.0/Documentation/Manual/LicenseActivationMethods.html) |
| C9 | コマンドラインでのライセンスの有効化の手順は、Unity Personal には当てはまらない。 | Unity のライセンスとバッチモード | standards_tools | primary | fact |  | verified | [Manage your license through the command line (Unity 6000.6)](https://docs.unity.com/en-us/engine/6000.6/manual/get-started/install-and-upgrade/licenses-and-activation/managing-your-unity-license) |
| C10 | Windows Sandbox は Windows の Home のエディションでは使えない。 | Windows の上での閉じ込め | standards_tools | primary | fact |  | verified | [Windows Sandbox (Microsoft Learn)](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-overview) |

## 抜粋

- C1：「Unity has deprecated the MCP server component of the in-editor AI assistant package」
- C2：「command in the Unity CLI replaces the in-Editor server」
- C3：「Unity MCP server is deprecated.」
- C4：「The bridge starts automatically when the Unity Editor loads.」
- C5：「November 16, 2026」
- C6：「Control the Unity Editor through a local HTTP API to automate builds, tests, and development workflows.」
- C7：「For Unity Personal, the Unity Hub is the only method for activating and returning licences.」
- C8：「This method isn't supported for Unity Pro assigned seats or Unity licenses under a Personal plan.」
- C9：「The following procedures don't apply to Unity Personal.」
- C10：「Windows Sandbox is currently not supported on Windows Home edition.」

## 確かめられなかったこと

- Unity CLI（1.0 系）を Personal のライセンスでバッチモードの代わりに使えるかは、文書で確認していない。
- Editor が同じプロジェクトを開いているときのバッチモードの振る舞いを書いた今の版の文書は見つからなかった（古い版の文書だけ）。手元で測った（実験と計画の M-07）。
- Unity Test Framework 1.6 のコマンドラインの文書の URL は 404 だった。-runTests の振る舞いは手元で測った。
- practitioners_at_scale・academia・frontier_labs・practitioners の分野は巡回していない（製品の仕様の確認なので、公式の文書だけを読んだ）。

## 検索語

- Unity official MCP server package docs.unity3d.com AI Assistant MCP 2026
- "Unity MCP" server deprecated "command-line interface" com.unity.ai.assistant manual
- Unity manual activate license command line headless Docker Personal license serial username password returnlicense
- Unity 6 manual license activation Personal license Hub only command line not supported Unity Licensing Server build automation
- Windows Sandbox prerequisites Windows 11 Pro Enterprise Education Home not supported learn.microsoft.com
- Unity manual command line arguments batchmode "another Unity instance is running with this project open"

## 問い

# Unity への接続の下調べ（G0-6）

## 段
compare

## 決めたい判断
Unity の実行を、Windows の Unity をアダプタがクローンに対して呼ぶ形（設計 §9 の ①）にするか、Linux の Unity をコンテナで動かす形（②）にするか。エージェントに Editor を操らせる口（公式の MCP）を使うか。

## 判断を変えうるもの
Personal のライセンスでコンテナの中のバッチモードを有効にできる記述。公式の MCP が Editor を開かずに使える記述。Home のエディションで使える閉じ込めの仕組みの記述。

## 候補
- ①：Windows の Unity をアダプタがクローンに対して呼ぶ（Core は .NET で分けてビルドする）
- ②：Linux の Unity をコンテナの中で動かす
- Editor を操る口：AI Assistant の MCP サーバー／Unity CLI／使わない（バッチモードだけ）

## 問い
Unity の公式の MCP の今の状態。Personal のライセンスの有効化の方法（コマンドライン・手動・Hub）。Windows Sandbox を使えるエディション。
