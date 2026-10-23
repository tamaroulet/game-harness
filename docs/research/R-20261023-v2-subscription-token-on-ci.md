# 定額の契約（Claude Pro / Max）のトークンを、自分のリポジトリの GitHub Actions で公式の Claude Code の Action に使ってよいか（照合の 2 回目）

- 記録：R-20261023-v2-subscription-token-on-ci
- 段：verify
- 決めたい判断：ADR-005 の D-3 の試しの走行で、1 人の操縦士の公開リポジトリの GitHub Actions に、操縦士本人の Claude Max の契約から作ったトークンを置き、公式の Claude Code GitHub Actions（anthropics/claude-code-action）を動かしてよいか。だめなら API キー（従量の課金）で試す
- 調べた日時：2026-10-23T15:06:45+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 12・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | 利用規約の第 3 条 7 項は、API キーでアクセスする場合と Anthropic が明示的に許可した場合を除き、ボット・スクリプトなどの自動の手段でサービスにアクセスすることを禁じている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Consumer Terms of Service](https://www.anthropic.com/legal/consumer-terms) |
| C2 | Claude Code の法務の文書は、Consumer Terms が Free・Pro・Max の利用者に適用されると案内している | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code](https://code.claude.com/docs/en/legal-and-compliance) |
| C3 | Claude Code の法務の文書は、OAuth 認証は Free・Pro・Max・Team・Enterprise の定額プランの購入者専用であり、Claude Code などのネイティブのアプリの通常の利用のために設計されていると述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code](https://code.claude.com/docs/en/legal-and-compliance) |
| C4 | Claude Code の法務の文書は、Anthropic が第三者の開発者に対し、自分のユーザーの代わりに Free・Pro・Max のプランの認証情報でリクエストを送ることを許していないと述べている。開発者は API キーを使うべきとしている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code](https://code.claude.com/docs/en/legal-and-compliance) |
| C5 | Claude Code の法務の文書は、エンドユーザーが改変されていない Claude Code のバイナリに、自分の Claude の定額プランで署名することは妨げないと述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code](https://code.claude.com/docs/en/legal-and-compliance) |
| C6 | Claude Code の法務の文書は、Pro・Max の公表された利用の上限は、Claude Code と Agent SDK の通常の個人の利用を前提にしていると述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code](https://code.claude.com/docs/en/legal-and-compliance) |
| C7 | Claude Code の認証の文書は、CI パイプラインやスクリプトなど、ブラウザでのログインができない環境のために、claude setup-token で 1 年有効の OAuth トークンを作るよう案内している | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Authentication - Claude Code](https://code.claude.com/docs/en/authentication) |
| C8 | Claude Code の認証の文書は、このトークンは Pro・Max・Team・Enterprise の契約が必要で、モデルへのリクエストのみに使えると述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Authentication - Claude Code](https://code.claude.com/docs/en/authentication) |
| C9 | Claude Code GitHub Actions の文書は、CLAUDE_CODE_OAUTH_TOKEN を、Claude の定額の契約で認証する OAuth トークンとして GitHub の秘密に置き、claude setup-token をローカルで実行して作るよう案内している。Pro・Max の契約者が対象 | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code](https://code.claude.com/docs/en/github-actions) |
| C10 | Claude Code GitHub Actions の文書は、OAuth トークンを GitHub Actions で使うと、実行は API の課金ではなく Claude の定額の契約を使うと述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code](https://code.claude.com/docs/en/github-actions) |
| C11 | Claude Code GitHub Actions の文書は、複数のリポジトリで共有する秘密には、OAuth トークンではなく API キーを使うよう勧めている。理由は、OAuth トークンが claude setup-token を実行した人の契約に結びつくため | API キー（従量課金） | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code](https://code.claude.com/docs/en/github-actions) |
| C12 | Anthropic のサポート記事は、Pro と Max の利用の上限が、claude.ai と Claude Code の活動で共有され、両方の活動が同じ上限に数えられると述べている | 定額の OAuth トークン | standards_tools | primary | fact | 不明 | verified | [Claude support article on usage limits (articles/11145838)](https://support.claude.com/en/articles/11145838) |

## 抜粋

- C1：「to access the Services through automated or non-human means, whether through a bot, script, or otherwise.」
- C2：「for Free, Pro, and Max users」
- C3：「is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications」
- C4：「Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users」
- C5：「Nor does it prevent an end user from signing in to the unmodified Claude Code binary with their own Claude subscription」
- C6：「Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK.」
- C7：「For CI pipelines, scripts, or other environments where interactive browser login isn't available, generate a one-year OAuth token with」
- C8：「This token authenticates with your Claude subscription and requires a Pro, Max, Team, or Enterprise plan」
- C9：「an OAuth token that authenticates with your Claude subscription, available on Pro, Max, Team, and Enterprise plans」
- C10：「If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.」
- C11：「since an OAuth token is tied to the subscription of the person who ran」
- C12：「These limits are shared across Claude and Claude Code, meaning all activity in both tools counts against the same limits.」

## 確かめられなかったこと

- 本文は WebFetch が要約モデルを通して返したテキストから写した。生の HTML との一字一句の照合は行っていない。機械照合で不一致が出る可能性がある。特に利用規約（consumer-terms）と支援記事（11145838）の抜粋は信頼度を medium とした
- 主張 1（利用規約の自動アクセス禁止）: 利用規約の本文に Claude Code・Agent SDK・setup-token への言及は見つからなかった。個人が自分のリポジトリの CI で本人のトークンを使うことが、この条項に当たるかは本文だけでは決まらない
- 主張 3（第三者への OAuth 認可の禁止）: 本文が禁じるのは、第三者の開発者が製品・サービスで Free・Pro・Max の認証情報をユーザーの代わりに使うことである。本人が自分のリポジトリの CI で自分のトークンを使うことを直接禁じる文は見つからなかった。「認めていない」と言い切れるのは、この範囲に限られる
- 主張 4（CI で使った分も同じ上限に数えられる）: 支援記事 11145838 は claude.ai と Claude Code の活動が上限を共有すると述べるが、CI・GitHub Actions・自動化・スクリプトへの言及は本文に無い（要約で確認）。GitHub Actions の文書は、OAuth トークンの実行は定額の契約を使うと述べる。よって「CI の分も同じ上限」は、この 2 つからの推定で、出典が直接述べてはいない
- 支援記事 11145838 の本文は、要約結果から確認した。上限の数字は記事の古い見積もりで、現在の値は確認できなかった
- 全ページで公開日を確認できなかった（不明）
- 標準の検索は 1 回だけ行った。CI でのトークン利用について、Anthropic の利用規約の改訂や別の公式の FAQ がある可能性は確かめていない

## 検索語

- Claude Pro Max usage limits Claude Code shared claude.ai support article
- fetch: https://code.claude.com/docs/en/authentication
- fetch: https://code.claude.com/docs/en/legal-and-compliance
- fetch: https://code.claude.com/docs/en/github-actions
- fetch: https://www.anthropic.com/legal/consumer-terms
- fetch: https://support.claude.com/en/articles/11145838

## 問い

# 定額の契約（Claude Pro / Max）のトークンを、自分のリポジトリの GitHub Actions で公式の Claude Code の Action に使ってよいか（照合の 2 回目）

## 段
verify

## 決めたい判断
ADR-005 の D-3 の試しの走行で、1 人の操縦士の公開リポジトリの GitHub Actions に、操縦士本人の Claude Max の契約から作ったトークンを置き、公式の Claude Code GitHub Actions（anthropics/claude-code-action）を動かしてよいか。だめなら API キー（従量の課金）で試す

## 判断を変えうるもの
次の主張のどれかが、Anthropic の一次の出典（利用規約・公式の文書）の本文に無いか、本文と食い違う。特に、定額の契約のトークンを CI で使うことを禁じる・制限する記述が見つかれば、API キーに替える

## 候補
1. 定額の契約の OAuth トークンを、公式の Claude Code GitHub Actions に使う
2. API キー（従量の課金）を使う

## 問い
次の主張が、Anthropic の一次の出典（anthropic.com/legal の Consumer Terms・Commercial Terms・Usage Policy、code.claude.com または docs.anthropic.com の Claude Code の文書、support.claude.com）の本文にあるかを、本文の抜粋つきで確かめてください。無い・食い違うときは、その旨と、食い違う本文を挙げてください。

1. Consumer Terms は、API キーを使う場合と Anthropic が明示的に許した場合を除き、ボット・スクリプトなどの自動の手段でサービスにアクセスすることを禁じている
2. Claude Code の GitHub Actions の公式の文書は、`claude setup-token` で作るトークン（`CLAUDE_CODE_OAUTH_TOKEN`）を、Pro / Max の契約者が GitHub の秘密に置いて Action を動かす構成を案内している
3. Anthropic は、定額の契約の OAuth トークンを、第三者の製品・サービス・Agent SDK から使うことを認めていない（開発者は API キーを使うべきとしている）
4. Pro / Max の利用の上限は、Claude Code の利用と claude.ai の利用で共有され、CI で使った分も同じ上限に数えられる
5. Claude Code の認証の公式の文書（https://code.claude.com/docs/en/authentication）は、`claude setup-token` で作る 1 年の OAuth トークンを、ブラウザでのログインができない CI のパイプラインとスクリプトに使うよう案内している
6. Claude Code の法務の文書（https://code.claude.com/docs/en/legal-and-compliance）は、Pro / Max の利用の上限が、Claude Code と Agent SDK の通常の個人の利用を前提にしていると述べている

抜粋は、ページの表示された本文の文字のとおりに写してください。Markdown の記号（バッククォート・アスタリスクなど）は付けないでください。
