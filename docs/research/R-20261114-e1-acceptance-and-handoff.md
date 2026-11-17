# 受け入れの検証の自動生成と、積み重ねの文脈の持ち方

- 記録：R-20261114-e1-acceptance-and-handoff
- 段：compare
- 決めたい判断：E-1（課題の文から受け入れの検証を作る作り方と、その物差し）と、E-3（中くらいの仕事を積み重ねるときの文脈の持ち方）の設計
- 調べた日時：2026-11-14T19:30:00+09:00
- 調べ役：copilot（）
- 照合：verified 14・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | SWT-Bench は SWE-bench を、イシューから再現のテストを作る課題に変え、作ったテストが修正前のコードで落ち、正解のパッチの後で通るか（fail-to-pass）で測る。作ったテストで修正のパッチを絞ると、再現率は 20% だが、SWE-Agent の適合率は 2 倍を超えて 47.8% になった。 | イシューからのテスト生成と正解での採点 | academia | primary | fact | 2024-06 | verified | [SWT-Bench: Testing and Validating Real-World Bug-Fixes with Code Agents](https://arxiv.org/html/2406.12952v3) |
| C2 | SWT-Bench の表 2 で、5 つの案から最良を選ぶ pass@5 でも、修正前のコードで落ちるテストを作れた課題は 62.7%、正解のパッチの後で通る（fail-to-pass）テストを作れた課題は 22.1%（課題の単位の割合。列は順に、走れた割合 93.1・成功 20.3・F→× 62.7・F→P 22.1・カバレッジの差 7.2）。 | 生成テストの妥当性（正解で落ちる割合） | academia | primary | fact | 2024-06 | verified | [SWT-Bench: Testing and Validating Real-World Bug-Fixes with Code Agents](https://arxiv.org/html/2406.12952v3) |
| C3 | Otter は、修正のパッチがまだ無い段階でイシューからテストを作る。TDD-Bench-Verified（449 課題）で、GPT-4o の Otter と Otter++ は、31.4% と 37% の課題で fail-to-pass のテストを作った（基準の方法は 18.7%）。 | 実装前の受け入れテストの生成 | academia | primary | fact | 2025-02 | verified | [Otter: Generating Tests from Issues to Validate SWE Patches](https://arxiv.org/html/2502.05368v2) |
| C4 | Otter の生成テストを修正前のコードでの落ち方で分けると（表 6、GPT-4o）、AssertionError で落ちたものは 170 件中 97 件（57.1%）が fail-to-pass、そのほかの落ちは 101 件中 40 件（39.6%）、例外（error）で落ちたものは 109 件中 4 件（3.7%）、修正前に通ったものは 69 件中 0 件だった。 | 実装前のテストの落ち方と妥当性 | academia | primary | fact | 2025-02 | verified | [Otter: Generating Tests from Issues to Validate SWE Patches](https://arxiv.org/html/2502.05368v2) |
| C5 | Meta の ACH は、懸念に特化した少数の変異（いまのテストで捕まらない誤り）を LLM で作り、その変異を殺すテストを作る。LLM の等価変異の検出は、適合率 0.79・再現率 0.47（簡単な前処理で 0.95・0.96）。 | 変異で導くテスト生成と等価変異の除去 | frontier_labs | primary | fact | 2025-01 | verified | [Mutation-Guided LLM-based Test Generation at Meta](https://arxiv.org/abs/2501.12862) |
| C6 | ACH の生成テストは、Messenger と WhatsApp の試験会で、エンジニアに 73% が受け入れられた（36% が privacy に関わると判じられた）。 | 変異で導くテスト生成と等価変異の除去 | frontier_labs | primary | fact | 2025-01 | verified | [Mutation-Guided LLM-based Test Generation at Meta](https://arxiv.org/abs/2501.12862) |
| C7 | Agentic PBT は、既存の Python のモジュールのコードと文書から性質を推し量って Hypothesis のテストを作り、不具合の報告を出す。100 のパッケージで、報告の 56% が有効な不具合、点の上位 21 件では 86% が有効だった。既存のコードの不具合探しの数で、実装前の仕様からの受け入れの検証の妥当性ではない。 | 性質テストの自動生成 | frontier_labs | primary | fact | 2025-10 | verified | [Agentic Property-Based Testing: Finding Bugs Across the Python Ecosystem](https://arxiv.org/abs/2510.09907) |
| C8 | TestGenEval（SWE-bench 由来の 1,210 組のコードとテストのファイル）で、既存のコードに対してテストを書かせても、最良の GPT-4o の平均のカバレッジは 35.2%。主な理由は、実行の推論の誤りと、複雑な経路での assertion の誤り。 | テスト生成の基礎の限界 | academia | primary | fact | 2024-10 | verified | [TestGenEval: A Real World Unit Test Generation and Test Completion Benchmark](https://arxiv.org/abs/2410.00752) |
| C9 | Handoff Debt は、コーディングのエージェントを決まった点で止めて後任に引き継がせ、4 つの引き継ぎ方（リポジトリだけ・生の記録・要約のメモ・構造化のメモ）を比べた（75 課題・181 の引き継ぎ点・後任のモデルごと 724 回）。3 つの後任のモデルで、文脈を持つ引き継ぎは、リポジトリだけに比べ、イベント数の中央値を 20〜59%、累積の入力トークンを 42〜63% 減らした。 | 区切りでの引き継ぎの方式 | academia | primary | fact | 2026-06-01 | verified | [Handoff Debt: The Rediscovery Cost When Coding Agents Take Over Interrupted Tasks](https://arxiv.org/abs/2606.02875) |
| C10 | GitHub Copilot の本番の記録（2026 年 6 月。320 万の利用者・1,300 万のセッション・7.61 億回の LLM の呼び出し）で、KV キャッシュの当たりは 1 ターンの中で平均 90%、ターンの境で 55% に落ち、モデルの切り替えや文脈の圧縮の後は大きく外れる。 | 続く会話と圧縮のキャッシュ | frontier_labs | primary | fact | 2026-07-30 | verified | [Agentic Coding in the Wild: Characterizing GitHub Copilot Traces at Production Scale](https://arxiv.org/abs/2608.00101) |
| C11 | SWE-EVO は、リリース単位の変更を、個別に仕様を付けた PR の列への分解（正解の分解）を意図して伏せて出し、最終の状態で採点する。1 つの課題に複数の PR が紐づくことがある。 | 長い課題のベンチの分解の扱い | academia | primary | fact | 2025-12 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios](https://arxiv.org/html/2512.18470v6) |
| C12 | SWE-EVO の付録 E の表 4 は、リリースノートだけの条件と、PR と課題の文を足した条件を比べ、文を足すと多くのモデルで解決が上がり、順位と傾向は保たれる（48 課題で 1 課題が 2.08 ポイント）。 | 企画の粒度（リリースノートだけ）と細かい仕様 | academia | primary | fact | 2025-12 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios](https://arxiv.org/html/2512.18470v6) |
| C13 | SWE-EVO の表 4 の gpt-5（2025-08-07）は、リリースノートだけで OpenHands 14.58%・SWE-agent 16.67%、PR と課題の文を足して 18.75%・20.83%。 | 企画の粒度（リリースノートだけ）と細かい仕様 | academia | primary | fact | 2025-12 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios](https://arxiv.org/html/2512.18470v6) |
| C14 | 古い観測を伏せるだけの文脈の管理と LLM の要約を、SWE-agent と SWE-bench Verified の上で、5 つのモデルの設定にわたって比べた一次の論文（R-20261113-v4-harness-principles#C31 の出典）。 | 文脈の管理 | academia | primary | fact | 2025-08 | verified | [The Complexity Trap: Simple Observation Masking Is as Efficient as LLM Summarization for Agent Context Management](https://arxiv.org/abs/2508.21433) |

## 抜粋

- C1：「this more than doubles the precision of SWE-Agent to」
- C2：「pass@5 93.1 93.1 20.3 20.3 62.7 62.7 22.1 22.1」
- C3：「creating fail-to-pass tests for 31.4% and 37% of the instances, respectively, whereas the baseline produced such tests for 18.7% of the instances」
- C4：「assertionfail 170 97 57.1 fail 101 40 39.6 error 109 4 3.7」
- C5：「ACH also deploys an LLM-based equivalent mutant detection agent that achieves a precision of 0.79 and a recall of 0.47」
- C6：「engineers accepted 73% of its tests」
- C7：「found that of the 21 top-scoring bugs」
- C8：「with the best model, GPT-4o, achieving an average coverage of only 35.2%」
- C9：「context-bearing handoffs reduce median agent events by 20-59% and cumulative prompt tokens by 42-63% relative to repository-only takeover」
- C10：「drastically invalidated after events like model switches or context compaction」
- C11：「This formulation intentionally withholds an oracle decomposition into a sequence of separately specified PRs」
- C12：「adding PR/issue context generally improves resolved rates across most models, while preserving the overall ranking and relative trends」
- C13：「gpt-5-08-07 14.58 16.67 18.75 20.83」
- C14：「We present a systematic comparison of these approaches within SWE-agent on SWE-bench Verified across five diverse model configurations」

## 確かめられなかったこと

- 調べ役は副操縦士（Antigravity）で、モデルの名と検索語は記録に無い。抜粋は会話の席が 2026-11-14 に取り直し、機械で照合した
- 中くらいの仕事を積み重ねる同じ土俵で、続く会話と観測の伏せ・区切りと構造化の引き継ぎ・別文脈の検証役を横に並べて比べた一次の実測は、見つからなかった（無いとは言えない）
- SWT-Bench で、正解の後でも落ちる生成テストの主な理由（口の名前の不一致か、振る舞いの誤りか）は、本文で確かめていない
- Otter の数は、修正前のコードがある課題（SWE-bench 由来）でのもの。機能を足す課題（実装の前は口が無い）に当てはまるかは分からない
- Agentic PBT の数は、既存のコードの不具合探しのもの。実装前の仕様から作る受け入れの検証の妥当性は測っていない
- SWE-EVO のデータには、PR ごとのテストの切り分けが無い。PR の列を中くらいの仕事の参照に使う手間は測っていない
- arXiv の abs の頁は % を「\%」と出し、html の頁は式を二重に出す。抜粋は、% と式を含まない区切りか、本文のテキストのとおりの表の行で取った

## 検索語

- （副操縦士への頼み。実際の検索語は記録に無い）LLM が仕様から作ったテストや性質の質を、正解の実装・変異・誤ったパッチで測った研究
- （同）中くらいの仕事を積み重ねる長い開発で、文脈の持ち方（続く会話と観測の伏せ・区切りの引き継ぎ・別文脈の検証役）を比べた一次の実測
- （同）SWE-EVO を、リリースノートからの分解や積み重ねの評価に使った先行例と、手元で回す手間

## 問い

# 受け入れの検証の自動生成と、積み重ねの文脈の持ち方（E-1・E-3 の設計の根拠）

## 段
compare

## 決めたい判断
E-1（課題の文から受け入れの検証を作る作り方と、その物差し）と、E-3（中くらいの仕事を積み重ねるときの文脈の持ち方）の設計

## 判断を変えうるもの
仕様から作ったテストが、正解の実装でほとんど通る実測（妥当性を正解で測る必要が薄い）。落ち方が妥当性を予測しない実測。中くらいの仕事の積み重ねで、文脈の持ち方を直接比べた一次の実測（E-3 が要らない）

## 候補
- 受け入れの検証の作り方：例のテスト／性質／口の約束と例と性質の組み合わせ
- 積み重ねの文脈の持ち方：続く会話と古い観測の伏せ／区切りの新しい会話と構造化の引き継ぎ／文脈を共有しない検証役を足す

## 問い
LLM が仕様（イシューや課題の文）から作ったテストや性質の質を、正解の実装・変異・誤ったパッチで測った一次の研究は、何を示すか。中くらいの仕事を積み重ねる長い開発で、文脈の持ち方を比べた一次の実測はあるか。SWE-EVO を、分解と積み重ねの評価に使うとき、何が揃っていて何が無いか。
