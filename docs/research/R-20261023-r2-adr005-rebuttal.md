# 自律開発のハーネスの上位の設計の見直しに反する証拠（反証）

- 記録：R-20261023-r2-adr005-rebuttal
- 段：refute
- 決めたい判断：次の 3 つの決定を採るか。1 人の操縦士と 1 台の Windows の PC で、AI のエージェントに自分の開発の仕組みを改善させながら開発を回す、小さな自律開発環境についての決定である
- 調べた日時：2026-10-23T14:20:12+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 12・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic の規約は、サブスクのOAuthトークンを第三者の開発者が Free/Pro/Max の認証情報経由で中継することを認めていない。開発者は API キーを使うべきとしている | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Legal and compliance - Claude Code Docs](https://code.claude.com/docs/en/legal-and-compliance) |
| C2 | 公式ドキュメントは、複数リポジトリで共有する秘密には、サブスクのOAuthトークンではなく API キーを勧めている。OAuthトークンは setup-token を実行した人のサブスクに紐づくため | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code Docs](https://code.claude.com/docs/en/github-actions) |
| C3 | 公開リポジトリでは、フォークからの pull request の実行に GitHub が秘密を渡さない。そのため公式の例のレビューは同一リポジトリのブランチだけで動く | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code Docs](https://code.claude.com/docs/en/github-actions) |
| C4 | GitHub の公式文書では、フォークから起動したワークフローには秘密が渡されない（GITHUB_TOKEN を除く）。pull_request_target は秘密を扱えるため、危険な構成になりうる | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Events that trigger workflows - GitHub Docs](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows) |
| C5 | GitHub の公式文書は、pull_request_target が同じリポジトリの秘密を扱う点を説明し、その危険として、フォークのコードの実行による秘密の盗用を挙げている | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Events that trigger workflows - GitHub Docs](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows) |
| C6 | GitHub Actions の定期実行（schedule）は既定のブランチからしか動かず、公開リポジトリでは 60 日間の活動が無いと停止する | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code Docs](https://code.claude.com/docs/en/github-actions) |
| C7 | Claude GitHub App は、Actions・Workflows・Contents など多くの権限を一括で持つ。インストール時に権限の一部だけを選べない | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code Docs](https://code.claude.com/docs/en/github-actions) |
| C8 | 公式ドキュメントは、サブスクの OAuth トークンで実行すると API 課金ではなくサブスクの枠を使うと述べ、実行の費用はサブスクの利用上限に依存する | CI上のエージェント実行 | standards_tools | primary | fact | 不明 | verified | [Claude Code GitHub Actions - Claude Code Docs](https://code.claude.com/docs/en/github-actions) |
| C9 | Anthropic の評価の解説は、同じ課題でも実行ごとに結果が変わることを示し、1 回の合否で判断しないよう述べている | トレース分析による改善 | frontier_labs | primary | fact | 2026-01-09 | verified | [Demystifying evals for AI agents - Anthropic Engineering](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C10 | Anthropic の評価の解説は、トレースではなく最終的な成果を採点するよう述べており、経路の読み取りを採点の根拠にすることに警告している | トレース分析による改善 | frontier_labs | primary | opinion | 2026-01-09 | verified | [Demystifying evals for AI agents - Anthropic Engineering](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C11 | Anthropic の評価の解説は、評価の点数を、誰かが詳細を調べるまで額面どおりに受け取らないよう述べている。点数だけの評価には限界があることを認めている | ベンチ点による計測 | frontier_labs | primary | opinion | 2026-01-09 | verified | [Demystifying evals for AI agents - Anthropic Engineering](https://anthropic.com/engineering/demystifying-evals-for-ai-agents) |
| C12 | Harness-Bench の論文は、5,194 の実行記録に基づき、エージェントの能力はモデルだけでなくモデルとハーネスの組み合わせごとに報告すべきだと結論づけている。ハーネスの改善は点の比較で測る必要があるという論拠になる | ベンチ点による計測 | academia | primary | fact | 不明 | verified | [Harness-Bench: Measuring Harness Effects across Models in Realistic Agent Workflows](https://arxiv.org/abs/2605.27922) |

## 抜粋

- C1：「Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users.」
- C2：「since an OAuth token is tied to the subscription of the person who ran」
- C3：「On public repositories, GitHub withholds secrets from runs triggered by fork pull requests, so the review runs only on pull requests from branches in the same repository.」
- C4：「secrets are not passed to the runner when a workflow is triggered from a forked repository」
- C5：「prevents execution of unsafe code from the head of the pull request that could alter your repository or steal any secrets you use in your workflow」
- C6：「GitHub runs scheduled workflows only from the default branch and, in public repositories, disables the schedule after 60 days without repository activity.」
- C7：「GitHub doesn't let you accept a subset.」
- C8：「If you authenticate with an OAuth token, runs use your Claude subscription instead of API billing.」
- C9：「a task that passed on one eval run might fail on the next」
- C10：「grade what the agent produced, not the path it took」
- C11：「we do not take eval scores at face value until someone digs into the details of the eval」
- C12：「These results suggest that agent capability should be reported at the model-harness configuration level」

## 確かめられなかったこと

- 候補1（使っていないコードの凍結・削除）に反する一次の出典は見つからなかった。削除後に戻した事後検証は検索で見つからず、見つかったのは一般的な解説（embeddedartistry、linearb など）と個別のコミットだけで、本文を確認していないため quote を取れなかった
- 候補1の『休止中のコードが誤って再有効化される』という指摘は検索結果の要約にあったが、本文の一字一句を確認できなかったので claims に入れていない
- ホステッドランナーの 1 ジョブ最大 6 時間は検索結果の要旨にあったが、GitHub の limits ページを取得して本文を確認していない。セルフホステッドの上限は情報源ごとに食い違い、確定できなかった
- quote は WebFetch が要約モデルを通して返した本文から写した。一字一句の一致は未確認で、機械照合で落ちる可能性がある。特に code.claude.com の文は表記（記号・改行）が元と違うおそれがある
- Claude Code GitHub Actions のセキュリティ文書（anthropics/claude-code-action の docs/security.md）と、プロンプト注入の危険（公開リポジトリの issue 本文などからの注入）は取得できなかった。候補2の『公開リポジトリでの危険』について一次の根拠が無い
- サブスクの OAuth トークンを定期実行（cron）の CI で使うことが許されるかは、公式の文書では明記が見つからなかった。claudeissues の質問は本文を確認できなかった
- 2026-02-19 の文書更新と 2026-01-09 のサーバー側の強制の日付は二次の記事（gigazine、winbuzzer など）の情報で、Anthropic の一次の変更履歴は確認していない。4 月 4 日の制限（OpenClaw 関連）は日付が不確かで、一次の出典が無い
- Anthropic の評価の解説の公開日は取得結果では 2026-01-09 だが、別のディレクトリでは 2026-03-18 とされていた。日付は確定していない
- Harness-Bench の論文の公開日は arXiv の番号（2605）から 2026 年 5 月と推定されるが、本文で確認できなかったため 不明 とした。論文の限界（単発実行の分散、ベンチの点のノイズ）の節は要旨に無く、全文を確認していない
- 候補3に反する『ベンチの点はトレースより劣る・偏る』と直接述べた一次の出典は、今回の検索では見つからなかった。反証として使えたのは、単発の結果の不安定さ（非決定性）と、ハーネス構成ごとに点が変わるという論文の指摘だけ
- 候補1（コードの凍結）と候補3（トレース分析）について、『支える証拠』（賛成側）は集めていない。反証の役割に限った
- map 段の分野カバーは対象外（refute 段のため）

## 検索語

- Claude Code GitHub Actions documentation limits authentication secrets fork pull requests
- Claude Code GitHub Actions OAuth token subscription usage policy automated
- Anthropic engineering demystifying evals for AI agents transcripts grading
- removing dead code regret reintroduced postmortem deleted code had to restore
- agent harness failure analysis from traces versus benchmark score noisy evaluation
- GitHub Actions job timeout 6 hours limit self-hosted runner pull_request_target fork secrets not available documentation
- Anthropic consumer terms OAuth token Claude Code third-party tools prohibited 2026

## 問い

# 自律開発のハーネスの上位の設計の見直しに反する証拠（反証）

## 段
refute

## 決めたい判断
次の 3 つの決定を採るか。1 人の操縦士と 1 台の Windows の PC で、AI のエージェントに自分の開発の仕組みを改善させながら開発を回す、小さな自律開発環境についての決定である

## 判断を変えうるもの
下の候補に反する一次の出典（失敗の事後検証・実測・提供元の文書の制約）があれば、その決定を書き直す

## 候補
1. 使っていない部品（約 4 週動いていない、コードの 43% を占める別のライン）を、凍結のタグに残して本線と CI から外す
2. 手元の PC で回している独自の常駐ループ（タスクの待ち行列・実装役の呼び出し・統合 PR）を、GitHub Actions の上の公式のコーディング エージェント（Claude Code GitHub Actions を定額の契約のトークンで動かす）に置き換える。合否は CI の必須チェックで決める
3. ハーネスの改善を、凍結した 10 本の課題のベンチの点で検出するのをやめ、実行の記録（トレース）を読む失敗の分析から改善を起こし、退行は決定論の試験で止める

## 問い
上の候補のそれぞれに**反する**証拠だけを集めてください。

- 1：使っていないコードを消す・凍結することの失敗例（後で戻すときの費用など）
- 2：GitHub Actions の上でコーディング エージェントを長く・多く回したときの失敗例と制約（実行時間・費用・認証・秘密の扱い・公開リポジトリでの危険・利用規約）
- 3：トレースの分析による改善が、点の測定より劣る・偏るという指摘。ベンチの点で改善を測るべきだという主張
