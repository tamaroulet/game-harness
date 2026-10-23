# 貼られた資料の出典は実在し、数字は出典と一致するか

- 記録：R-20261023-q1-pasted-sources
- 調べた日時：2026-10-23T12:15:03+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 9・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic の監視先で、モノレポに入る変更の 1 日あたりの件数が 2026 年 5 月の約 2,500 件から 9 月には 30,000 件超に増えた。これは AI のレビュー件数ではなく、マージされた変更の件数である | vendor_claim | 2026-10-15 | verified | [Solving code review at Anthropic（claude.dev 掲載。元は Anthropic の記事を引用）](https://claude.dev/blog/solving-code-review-at-anthropic/) |
| C2 | Anthropic の Code Review 導入後、社内で実質的なレビューコメントを受けた PR の割合は 16% から 54% に上がった | vendor_claim | 2026-03-09 | verified | [Bringing Code Review to Claude Code](https://claude.com/blog/code-review) |
| C3 | Anthropic の社内での Code Review の結果として、1,000 行を超える大きな PR の 84% で指摘があり、平均 7.5 件の指摘が出た | vendor_claim | 2026-03-09 | verified | [Bringing Code Review to Claude Code](https://claude.com/blog/code-review) |
| C4 | Faros AI の研究では、AI の採用が進んだチームで PR のレビュー時間が 91% 増えた | vendor_claim | 2025-11-25 | verified | [Are AI coding assistants really saving time? (Faros AI)](https://www.faros.ai/blog/are-ai-coding-assistants-really-saving) |
| C5 | Faros AI の AI 生産性に関する分析は、10,000 人の開発者と 1,255 の企業のエンジニアリングチームのテレメトリと作業データに基づく | vendor_claim | 不明 | verified | [AI productivity paradox (Faros AI)](https://www.faros.ai/ai-productivity-paradox) |
| C6 | arXiv:2604.19049 は、OpenSSL の CMS モジュールにある存在しない Bleichenbacher パディングオラクルを、10 人の専任レビュアーが全員一致で支持した失敗例を挙げている。これは 1 回の経験的テストで初めて否定された | fact | 2026-04-21 | verified | [Refute-or-Promote: An Adversarial Stage-Gated Multi-Agent Review Methodology for High-Precision LLM-Assisted Defect Discovery](https://arxiv.org/abs/2604.19049) |
| C7 | 論文の著者は、この手法の貢献は、LLM エージェントの持続的な誤検出を外側の構造で絞り込む点にあり、自律的に発見した脆弱性ではないと述べている | opinion | 2026-04-21 | verified | [Refute-or-Promote: An Adversarial Stage-Gated Multi-Agent Review Methodology for High-Precision LLM-Assisted Defect Discovery](https://arxiv.org/abs/2604.19049) |
| C8 | Addy Osmani は、コードが読まれずにマージされることが普通になったと書いている | opinion | 2026-06-15 | verified | [Agentic Code Review (Addy Osmani)](https://addyosmani.com/blog/agentic-code-review/) |
| C9 | Addy Osmani は、レビューを省いても作業はなくならず、より高い代償で後に先送りされるだけだと主張している | opinion | 2026-06-15 | verified | [Agentic Code Review (Addy Osmani)](https://addyosmani.com/blog/agentic-code-review/) |

## 抜粋

- C1：「In Anthropic's monorepo, changes landing per day went from about 2,500 in May to more than 30,000 in September」
- C2：「Before, 16% of PRs got substantive review comments. Now 54% do.」
- C3：「on large PRs (over 1,000 lines changed), 84% get findings, averaging 7.5 issues.」
- C4：「Faros's research revealed a critical finding: teams with high AI adoption saw PR review time increase by 91%.」
- C5：「We analyzed telemetry and workflow data from 10K devs across 1,255 enterprise engineering teams.」
- C6：「The most instructive failure: ten dedicated reviewers unanimously endorsed a non-existent Bleichenbacher padding oracle in OpenSSL's CMS module; it was killed only by a single empirical test, motivating the mandatory empirical gate.」
- C7：「No vulnerability was discovered autonomously; the contribution is external structure that filters LLM agents' persistent false positives.」
- C8：「code began merging unread, and that became normal.」
- C9：「Skipping review without a safety net does not remove the work, it defers it at a higher price」

## 確かめられなかったこと

- 「30,000 件/日の AI コードレビュー」という数字は見つからなかった。確認できたのは、モノレポにマージされる変更が 1 日 30,000 件超という数字（レビュー件数ではない）だけである。原典は anthropic.com/engineering/redesigning-source-control と思われるが、未確認
- Anthropic の Code Review の記事（claude.com/blog/code-review、2026-03-09）には 1 日あたりの PR やレビューの件数はない。検索で見つかった claude.dev の記事の引用元も確認できていない
- Faros の 31.3% と 57.9% は、確認したページにも検索結果にも見つからなかった。調べた範囲では、同じ Faros のページにある 31%（「31% more code is reaching production with no review at all」）は別の指標であり、公開日は不明である。このページは PDF の全文ではなく、マーケティングページの要約である。元の報告書は未確認
- Faros の「ai-productivity-paradox」ページには公開日が表示されず、報告書の名前は「AI Engineering Report 2025」とされているが、年の確定はできなかった
- arXiv 2604.19049 は要旨の抜粋のみ確認した。PDF の全文は読んでいない。「反証の段」にあたる語が本文でどう定義されているかは未確認
- Addy Osmani の記事に、「AI が書いたコードを人が読まずに検証の網で受け入れる」という考えを直接述べた箇所は見つからなかった。近い内容は 2026-06-15 の記事にあるが、検証の網という語そのものはない。検索で見つかった O'Reilly の書籍ページ（2027 年 9 月）は、日付から見て仮のものの可能性がある
- Lauren Tan の講演や発言の一次資料は見つからなかった。Medium と newsletter の二次記事はあるが、PR 数が「約 1,000 件/月」と「2,000 件/月」で食い違っており、勤務先の名称も異なる。本文を確認していないため、引用できる文字列はない
- 今回の論文の要旨で述べられた OpenSSL の事例は、論文の本文で確認していない。要旨の文の一部は、WebFetch の要約から写したものであり、原文との一字一句の一致は機械で確かめる必要がある

## 問い

# 貼られた資料の出典は実在し、数字は出典と一致するか

次の出典が実在するか、そして次の数字・主張が出典の本文にあるかを確かめてください。

1. Anthropic の Claude Code チームが、コードレビュー（AI によるレビュー）について書いた記事。社内で 1 日あたり約 30,000 件の何かを処理しているという数字
2. Faros AI の報告（AI の生産性のパラドックスに関するもの）。31.3%・57.9%・91% という数字が何を指すか
3. arXiv:2604.19049（Refute-or-Promote と呼ばれる、複数のエージェントの合意に反証の段を挟む手法）。OpenSSL での誤報の例
4. Addy Osmani の、AI が書いたコードを人が読まずに検証の網で受け入れる考え方の記事
5. Lauren Tan の、AI のエージェントとコードレビューについての発言

見つからないもの、数字が合わないもの、時期の合わないもの（例：2024 年の報告に 2026 年の実測）は unknowns に書いてください。
