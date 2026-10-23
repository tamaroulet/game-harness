# AI のエージェントが書いた変更を、リスク（影響の大きさ・触るパス）で分けて承認・自動マージする実践

- 記録：R-20261023-q2-risk-tiered-approval
- 調べた日時：2026-10-23T12:16:21+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 12・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|
| C1 | GitHub は、CODEOWNERS の review request が届くには、CODEOWNERS ファイルが pull request のベースブランチに存在している必要があると説明している。 | fact | 不明 | verified | [About code owners - GitHub Docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) |
| C2 | GitHub は、ブランチ保護で code owner のレビューを必須にした場合、そのオーナーのいずれか一人の承認で要件を満たすと説明している。 | fact | 不明 | verified | [About code owners - GitHub Docs](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners) |
| C3 | GitHub のルールセットでは、必要な承認数を 0 から 10 の間で設定できると説明している。 | fact | 不明 | verified | [Available rules for rulesets - GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) |
| C4 | GitHub のルールセットには、code owner によるレビューを任意で必須にする選択肢がある。 | fact | 不明 | verified | [Available rules for rulesets - GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) |
| C5 | GitHub のルールセットでは、特定のファイルパターンに応じて、チームごとに必要な承認数を指定できる。最大 15 チームまで指定できる。 | fact | 不明 | verified | [Available rules for rulesets - GitHub Docs](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets) |
| C6 | OpenSSF Scorecard の Code-Review チェックでは、AI/ML を含む bot によるレビューは、コードレビューとして数えない。 | fact | 不明 | verified | [scorecard/docs/checks.md - ossf/scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md) |
| C7 | OpenSSF Scorecard は、bot のレビューを数えない理由として、第二の人物がその変更を理解しているという保証を与えないことを挙げている。 | opinion | 不明 | verified | [scorecard/docs/checks.md - ossf/scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md) |
| C8 | Intercom は、同社の主要な 2 つのコードベースの pull request のうち 93% 超が Agent 主導であると述べている（自社の報告）。 | vendor_claim | 2026-04-21 | verified | [AI is approving our pull requests: Here's how we made it safe - The Intercom Blog](https://www.intercom.com/blog/ai-is-approving-our-pull-requests-heres-how-we-made-it-safe) |
| C9 | Intercom は、Agent 主導の pull request のうち 19% 超が、人間のレビュアーなしで自動承認されていると述べている（自社の報告）。 | vendor_claim | 2026-04-21 | verified | [AI is approving our pull requests: Here's how we made it safe - The Intercom Blog](https://www.intercom.com/blog/ai-is-approving-our-pull-requests-heres-how-we-made-it-safe) |
| C10 | Intercom は、どのエンジニアも、どの変更についても、いつでも人間のレビューを要求できると述べている。 | vendor_claim | 2026-04-21 | verified | [AI is approving our pull requests: Here's how we made it safe - The Intercom Blog](https://www.intercom.com/blog/ai-is-approving-our-pull-requests-heres-how-we-made-it-safe) |
| C11 | Ona は、AI エージェントが低リスクの pull request を自動承認する運用を記述しており、その結果リードタイムが 74% 短縮されたと主張している。 | vendor_claim | 2026-04-09 | verified | [How auto-approving low-risk PRs with AI cut our lead time by 74% · Ona](https://ona.com/stories/auto-approving-low-risk-prs) |
| C12 | Ona の運用では、AI が低リスクの PR を承認しても、マージは必ず人間が行う。 | vendor_claim | 2026-04-09 | verified | [How auto-approving low-risk PRs with AI cut our lead time by 74% · Ona](https://ona.com/stories/auto-approving-low-risk-prs) |

## 抜粋

- C1：「For code owners to receive review requests, the CODEOWNERS file must be on the base branch of the pull request.」
- C2：「When reviews from code owners are required, an approval from any of the owners is sufficient to meet this requirement.」
- C3：「Required approvals can be set from 0 (zero) to 10.」
- C4：「Optionally, you can choose to require reviews from code owners.」
- C5：「You can specify up to 15 different teams, and for each team you can require a certain number of approvals from team members.」
- C6：「Review by bots, including bots powered by artificial intelligence / machine learning (AI/ML), do not count as code review.」
- C7：「Such reviews do not provide confidence that there will be a second person who understands the code change」
- C8：「Today, over 93% of our pull requests (PRs) across our two main codebases are Agent-driven.」
- C9：「And over 19% are auto-approved with no human reviewer in the loop.」
- C10：「Any engineer can request a human review on any change, at any time.」
- C11：「How auto-approving low-risk PRs with AI cut our lead time by 74%」
- C12：「A human always merges.」

## 確かめられなかったこと

- Vercel の記事（58% of PRs in our largest monorepo merge without human review）は、検索で URL を得たが、本文の取得が 404 で失敗したため、主張と一字一句の抜粋を確認できなかった。このため claims に入れていない。
- Anthropic 自身の内部方針（低リスクは人間の承認なし、通常は 1 人、最高リスクは 2 人）は、二次的な解説記事でしか確認できなかった。一次の出典は見つからなかった。
- Ona の記事に書かれた「engineers cannot self-classify a change as low-risk」は、取得した要約で言い換えられていた可能性があり、一字一句の抜粋を確認できなかったため claims に入れていない。
- ルールセットでは『最も厳しい版が適用される』と書かれているという検索スニペットがあったが、本文での一字一句の確認ができなかったため、claims に入れていない。
- 層別の失敗例（誤った自動マージ、誤分類による本番障害など）について、一次の事後検証（postmortem）は見つからなかった。Scorecard の issue 等の事例はあるが、層別そのものの失敗を示すものではない。
- 層別をしない（すべて人間承認、またはすべて自動）ことを勧める一次の意見は、今回の検索では見つからなかった。
- Intercom の記事と Ona の記事は、いずれも自社の運用報告であり、独立した検証は行われていない。
- GitHub の rulesets のドキュメントは、取得した時点で 0〜10 の承認数を示していたが、REST API の branch protection 側では別の上限が示された検索スニペットもあり、版ごとの差がありうる。現行の文書で確認が必要。
- Scorecard の checks.md は、古い版では bot のみの履歴を『inconclusive』としていた、という検索スニペットがあったが、現行版での一字一句の確認はしていない。

## 問い

# AI のエージェントが書いた変更を、リスク（影響の大きさ・触るパス）で分けて承認・自動マージする実践

AI のコーディング エージェントが書いたプルリクエストを、影響の大きさ（触るファイルのパス・変更の種類）で層に分け、低い層は自動でマージし、高い層（CI・権限・承認の仕組み・依存の版）は人が承認する、という運用の、いま公開されている一次の出典を集めてください。

- 企業の技術ブログ・公式の文書・カンファレンスの発表を優先してください
- GitHub の CODEOWNERS・ブランチ保護・rulesets を、エージェントの変更の層別に使っている例があれば含めてください
- 層別をしない（すべて人が承認する、またはすべて自動）ことを勧める意見や、層別の失敗例も含めてください
