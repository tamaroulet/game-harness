# AI のコーディング エージェントで開発を回す環境が「良くなった」ことを、日々の実績から測る指標（俯瞰）

- 記録：R-20261023-r3-improvement-metrics-map
- 段：map
- 決めたい判断：1 人の操縦士が、AI のエージェントに台帳のタスクを実装させ、CI の試験で合否を決めて PR をマージする開発環境で、環境（ハーネス：指示・道具・門・手順）の改善を何で測り、何をもって「改善した」と判定するか。今の物差し（凍結した 10 本の課題のベンチの点、10 本中 7 本が満点に張り付き）は捨てる前提で、代わりを決める
- 調べた日時：2026-10-23T16:31:06+09:00
- 調べ役：claude-deep（claude-haiku-5-5, claude-sonnet-5-5）
- 照合：verified 11・quote_not_found 2・unreachable 1・no_source 1
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | Anthropic は Claude Code の貢献メトリクス（GitHub 連携）を公開ベータにした。社内では Claude Code の採用が進むにつれ、エンジニア 1 人・1 日あたりのマージ済み PR が 67% 増えたと報告している。PR 数だけでは速度の指標として不完全だとも書いている。 | マージ PR 数（人・日あたり） | frontier_labs | primary | vendor_claim | 2026-01-29 | verified | [Understand Claude Code's impact with contribution metrics](https://claude.com/blog/contribution-metrics) |
| C2 | GitHub は Copilot usage metrics API に、coding agent が作ってマージされた PR の数と PR のサイクルタイムを加えた（企業単位。のちに組織単位でも同じ指標が使える）。 | プラットフォーム提供の PR 計器（マージ数・所要時間） | standards_tools | primary | fact | 2026-02-19 | verified | [Pull request throughput and time to merge available in Copilot usage metrics API](https://github.blog/changelog/2026-02-19-pull-request-throughput-and-time-to-merge-available-in-copilot-usage-metrics-api/) |
| C3 | Claude Code は OpenTelemetry で、費用・トークン・PR 数・コミット数・コード編集の許可判断・稼働時間を計器として出力できる。 | OpenTelemetry による費用・トークン・介入の計器 | standards_tools | primary | fact | 不明 | quote_not_found | [Claude Code monitoring (OpenTelemetry)](https://code.claude.com/docs/en/monitoring-usage) |
| C4 | Cognition は Devin の PR のマージ率が前年の 34% から 67% に上がったと報告している。ただしこの記事は分母や期間を定義しておらず、課題の種類とも結びつけていない。 | エージェント PR のマージ率 | practitioners_at_scale | primary | vendor_claim | 2025-11-14 | verified | [Devin's 2025 Performance Review: Learnings From 18 Months of Agents At Work](https://cognition.com/blog/devin-annual-performance-review-2025) |
| C5 | Stripe の Minions は、人が書かずに人が審査するエージェント製 PR を週 1,300 件超マージしている（2026 年 2 月時点）。二次の出典によれば、仕事は設定・アップグレード・小さな整理が中心で、件数が大きな機能変更の証拠にはならない。CI は最大 2 回に制限している。 | マージ PR 数（週あたり）と CI 回数の上限 | practitioners_at_scale | secondary | fact | 2026-02 | verified | [Stripe Minions (research notes)](https://rywalker.com/research/stripe-minions) |
| C6 | Greptile は 2026 年 3〜4 月のマージ済み PR 1,000 件あたりの revert 数を公表した。Codex 1.19、Claude 1.80、人間 2.72、Cursor の背景エージェント 3.41、Devin 3.50。Greptile は PR レビューの売り手なので、数字は売り手のデータである。 | revert 率・手戻り率 | practitioners_at_scale | marketing | vendor_claim | 2026-04 | verified | [Greptile agent leaderboard](https://www.greptile.com/agent-leaderboard) |
| C7 | AIDev データセット上の分析で、エージェントの PR は人間の PR よりマージ率が低いと報告されている。マージの成否は、レビュー関連の特徴よりも投稿者の属性に強く左右される。 | エージェント PR の大量分析（マージ要因） | academia | secondary | fact | 2026-01-26 | verified | [Let's Make Every Pull Request Meaningful: An Empirical Analysis of Developer and Agentic Pull Requests](https://arxiv.org/html/2601.18749v1) |
| C8 | エージェントの修正系 PR でマージされなかった 326 件を調べた研究がある。テスト失敗と、同じ課題が別の PR で先に解決されていたことが、非マージの主な原因だった。ビルドやデプロイの失敗は少なかった。 | 非マージの原因分類 | academia | secondary | fact | 2026-01-29 | verified | [Why Are AI Agent–Involved Pull Requests (Fix-Related) Remain Unmerged? An Empirical Study](https://arxiv.org/html/2602.00164) |
| C9 | MSR 2026 の研究（2025 年 6〜8 月、オープンソースの約 11 万件の PR）は、エージェントが書いたコードは人間のコードより時間がたつと書き直される（churn が多い）ことを示した。生存率と churn を縦断で見ている。 | コードの生存率・churn（縦断） | academia | secondary | fact | 不明 | unreachable | [Investigating Autonomous Agent Contributions in the Wild: Activity Patterns and Code Change over Time](https://huggingface-gw-kz.just-ai.com/papers/2604.00917) |
| C10 | METR の無作為化試験（経験のある開発者 16 人、246 件の実課題）では、AI を使うと作業時間が 19% 長くなった。開発者は 24% の短縮を予想していた。 | 無作為化比較（課題ごとに AI 可否を割り当て） | frontier_labs | primary | fact | 2025-07-10 | verified | [Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity](https://metr.org/blog/2025-07-10-early-2025-ai-experienced-os-dev-study/) |
| C11 | METR の 2026 年 2 月の追試は、開発者が AI なしでの参加を拒み、標本が偏ったと報告した。そのため、速度向上の大きさの証拠としてはごく弱いと自ら述べている。実運用の指標が偏る条件を示す例である。 | 無作為化比較（選択バイアスと限界） | frontier_labs | primary | fact | 2026-02-24 | verified | [We are Changing our Developer Productivity Experiment Design (uplift update)](https://metr.org/blog/2026-02-24-uplift-update/) |
| C12 | METR の追試では信頼区間が広い。元の開発者は 18% の短縮（区間は -38% から +9%）、新規の開発者は 4% の短縮（区間は -15% から +9%）だった。小さな標本の幅の実例になる。 | 無作為化比較（区間の幅） | frontier_labs | primary | fact | 2026-02-24 | verified | [We are Changing our Developer Productivity Experiment Design (uplift update)](https://metr.org/blog/2026-02-24-uplift-update/) |
| C13 | Epoch AI は OpenAI の公開 Codex リポジトリのマージ済み PR 7,524 件を、3 つのモデルによる判定で分析した。得られるのは節約時間の上限であり、因果の証拠にはならないと自ら述べている。 | LLM 判定による PR ごとの工数推定 | practitioners | secondary | fact | 2026-07-07 | verified | [Codex engineer effort (Epoch AI)](https://epoch.ai/data-insights/codex-engineer-effort) |
| C14 | Johari らは、検定を続けて監視し、好きな時に止めてよい「いつでも有効な」p 値と信頼区間を定義した。固定標本の検定は、標本数を監視しながら決めると信頼できなくなるためである。逐次検定の標準的な出典。 | 逐次検定（いつでも有効な推論） | academia | primary | fact | 2015-12-15 | quote_not_found | [Always Valid Inference: Bringing Sequential Analysis to A/B Testing](https://arxiv.org/abs/1512.04922) |
| C15 | 管理図（XmR）は、標準偏差ではなく移動範囲の平均から限界を求める。スプリントのように期ごとに 1 点しかない小さな系列に向く方法と説明されている。ただしこれは売り手の文書とブログによる説明である。 | 管理図（XmR） | standards_tools | marketing | opinion | 不明 | no_source | [To create Xm and R control charts](https://docs.infor.com/lnaqm/10.5/en-us/lnaqmolh/c_qm_create_xm_rcontrol_charts.html) |

## 抜粋

- C1：「As Claude Code adoption has increased internally, we've seen a 67% increase in PRs merged per engineer per day.」
- C2：「pull requests created with Copilot coding agent that got merged」
- C3：「／ `claude_code.cost.usage` ／ Cost of the Claude Code session ／ USD ／」
- C4：「67% of its PRs are now merged vs 34% last year」
- C5：「the system produces over 1,300 merged pull requests per week」
- C6：「Lower is better. The dashed line is the human baseline for the same merge window.」
- C7：「submitter attributes dominate merge outcomes for both groups, while review-related features exhibit contrasting effects between human and agentic PRs.」
- C8：「test case failures and prior resolution of the same issues by other PRs are the most common causes of non-integration」
- C9：「Ultimately, our findings indicate an increasing agent activity in open-source projects, although their contributions are associated with more churn over time compared to human-authored code.」
- C10：「Surprisingly, we find that when developers use AI tools, they take 19% longer than without—AI makes them slower.」
- C11：「our data is only very weak evidence for the size of this increase.」
- C12：「a speedup of -18% with a confidence interval between -38% and +9%.」
- C13：「the implicit uplift shown here corresponds to an upper bound on time saved.」
- C14：「these inferences are wholly unreliable if users endogenously choose samples sizes by continuously monitoring their tests.」
- C15：「」

## 確かめられなかったこと

- 【注意】最後の claim（管理図 XmR）は quote が空で、写せていない。機械の照合では落ちる。claims から除くか、出典を取り直す必要がある
- DORA 2025 の本文（AI 導入でスループットは改善するが不安定さは増える、AI 能力モデルの 7 項目）は、取得ツールの制限で本文の抜粋を一字一句写せなかった。検索結果の要約にあるだけなので、claims には入れていない。frontier_labs（Google）の DORA は未照合
- SPACE の枠組みをエージェントに当てた研究は見つからなかった
- OpenAI の社内指標の一次の出典（Codex の採用に関する論文、harness engineering の記事）は取得できなかった。二次の報告（社員の約 98% が利用、マージ PR が約 70% 増など）は、OpenAI 自身の数字で未検証
- Ramp Inspect（マージ PR の過半）と Stripe の数字は、二次のまとめ記事経由のみ。Stripe・Ramp の一次の記事は未取得
- Cursor の背景エージェントの運用指標に関する、Cursor 自身の公式の出典は見つからなかった
- GitHub 文書の『PR レビューを Copilot が行った PR のマージ指標』（2026-04-08）と、リポジトリ単位の指標（2026-07-17）は検索結果で確認しただけで、本文の引用は取っていない
- Anthropic の社内調査（生産性が 50% 向上という自己申告）は、一次の出典を確認できなかった
- CodeRabbit（AI 共著の PR で問題が約 1.7 倍）と Faros AI（churn 861% 増など）の数字は、孫引きのみ。一次の報告は未取得
- Greptile の revert 率は売り手のデータで、MSR 2026 の研究（Codex も churn が多い）と向きが食い違う。期間・エージェントの構成・対象が違う
- 小さな標本（1 人・週に数十件）で、逐次検定・ベイズ・管理図を使う方法を、ソフトウェア開発に当てた一次の出典は見つからなかった。Johari らは A/B テストの一般論で、小標本での検出力の低下には触れていない。ベイズ法の出典は未取得
- 管理図の出典は、売り手の文書とブログのみ。Wheeler や Vacanti の本文は取得できなかった
- 個人運用の少数標本について、実運用の指標が偏る・使えない証拠は、METR の追試（選択バイアス、時間測定の不確かさ）が唯一の直接例

## 検索語

- AI coding agent pull request merge rate empirical study AIDev dataset
- DORA 2025 State of AI-assisted Software Development report AI capabilities model
- METR randomized controlled trial AI developer productivity experienced open-source developers
- GitHub Copilot coding agent metrics pull requests merged dashboard usage metrics API
- Anthropic how we measure Claude Code productivity internal usage survey merged pull requests per engineer
- METR 2026 follow-up developer productivity AI study selection effects
- sequential testing small sample A/B always-valid inference single developer experiments
- Cursor background agents engineering blog measuring agent merged PRs; Stripe minions; Ramp inspect background agent percentage of merged PRs
- Devin annual performance review PR merge rate Cognition
- statistical process control control chart software engineering metrics small sample XmR chart Vacanti Actionable Agile
- AI coding agent PR revert rate rework churn code quality GitClear OR CodeRabbit report 2026
- OpenAI Codex how we measure internal adoption PRs merged engineers Codex 2026 engineering blog

## 問い

# AI のコーディング エージェントで開発を回す環境が「良くなった」ことを、日々の実績から測る指標（俯瞰）

## 段
map

## 決めたい判断
1 人の操縦士が、AI のエージェントに台帳のタスクを実装させ、CI の試験で合否を決めて PR をマージする開発環境で、環境（ハーネス：指示・道具・門・手順）の改善を何で測り、何をもって「改善した」と判定するか。今の物差し（凍結した 10 本の課題のベンチの点、10 本中 7 本が満点に張り付き）は捨てる前提で、代わりを決める

## 判断を変えうるもの
最前線の企業・大規模な実務家・研究が、エージェントの開発の生産性や品質を、ベンチの点ではなく実運用の記録（マージ率・手戻り・人の介入・費用・退行など）で測っている例があれば、その指標と判定の仕方を採る。逆に、実運用の指標が小さな標本では使えない・偏るという証拠があれば、その条件を判定に入れる

## 分野
frontier_labs practitioners_at_scale academia standards_tools practitioners

## 問い
AI のコーディング エージェント（Claude Code・Codex・Copilot の coding agent・Devin・Cursor の背景エージェントなど）で開発を回すとき、エージェントやその周りの仕組み（ハーネス）の改善を、実運用の記録で測る指標と判定の仕方を、分野ごとに集めてください。

- 提供元（Anthropic・OpenAI・GitHub・Google など）が自社や顧客の実運用で使っている指標（例：エージェントの PR のマージ率、1 回目で通る率、人の介入の回数、1 件あたりの費用とトークン、差し戻し・revert・後で見つかった不具合の率、所要時間）
- 大規模な実務家の報告（社内でエージェントを広く使う企業の測り方と、指標が誤った方向に引っ張った例）
- 研究（DORA・SPACE などの開発の生産性の枠組みをエージェントに当てた研究、エージェントの PR を大量に分析した研究、実運用の指標の統計的な扱い）
- 道具と標準（GitHub・CI・観測の道具が提供している、エージェントの作業の計器）
- 小さな標本（1 人・週に数十件）で改善を判定するときの統計の扱い（逐次検定・ベイズ・管理図など）を勧める出典
