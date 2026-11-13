# 公式の長い課題のベンチの比較：SWE-EVO と FeatureBench（と自作の鎖の課題）

- 記録：R-20261113-v4-benchmarks
- 段：compare
- 決めたい判断：v4 の比べの土俵（素の A の校正・ハーネスの開発・held-out の証明）に、どのベンチをどの順で使うか
- 調べた日時：2026-11-13T18:00:12+09:00
- 調べ役：seat（claude-opus-5-5）
- 照合：verified 18・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | SWE-EVO は 48 のリリースの大きさの課題で、平均 21 ファイルにわたる変更、課題あたり平均 874 のテストで確かめる。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C2 | SWE-EVO では最高のモデルでも 25%。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C3 | gpt-5.2 は SWE-Bench Verified の 72.80% から SWE-EVO の 22.92% に落ちる。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C4 | gpt-5 の失敗の 6 割超は指示の読み違い（リリースノートの解釈）。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C5 | 48 課題なので、1 課題で解決率が 2.08 ポイント動く。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C6 | 課題の分布は dvc に偏っている。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C7 | SWE-Bench は単発の課題では飽和の兆しがある。 | SWE-EVO | academia | secondary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C8 | SWE-EVO の評価は、同梱の SWE-bench の evaluate_instance.py で軌跡を採点する。 | SWE-EVO | standards_tools | primary | fact |  | verified | [SWE-EVO の README](https://raw.githubusercontent.com/SWE-EVO/SWE-EVO/main/README.md) |
| C9 | FeatureBench の最初の版は、24 のリポジトリから 200 の評価の課題と 3,825 の実行できる環境。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C10 | SWE-bench で 74.4% の Claude 4.5 Opus も、FeatureBench では 11.0% しか解けない。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C11 | 正解の単体テストを見せると、解決率も通ったテストの割合も大きく上がった。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C12 | NameError が最多で、ファイルをまたぐ依存の解決に弱い。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C13 | ファイルを読まずに、別のファイルの部品のインターフェースや属性を推測（幻覚）する怠けがある。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C14 | どのモデルも 1 課題で 100 万を超える入力トークンを使う。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C15 | 手数の上限を 50 から 100 にすると上がるが、100 を越えると伸びはわずか。 | FeatureBench | academia | primary | fact | 2026-02 | verified | [FeatureBench: Benchmarking Agentic Coding for Complex Feature Development](https://arxiv.org/html/2602.10975) |
| C16 | Fast の分け（100 課題）は GPU が要らず、速い評価に向く。 | FeatureBench | standards_tools | primary | fact |  | verified | [FeatureBench の README](https://raw.githubusercontent.com/LiberCoders/FeatureBench/main/README.md) |
| C17 | Lite の順位で、Gemini-3-Pro-Preview と Gemini CLI の組は通ったテストの割合 43.4%・解決 10%。 | FeatureBench | standards_tools | primary | fact |  | verified | [FeatureBench の README](https://raw.githubusercontent.com/LiberCoders/FeatureBench/main/README.md) |
| C18 | データセットの V1.1 は、課題の問題文の食い違いと抜けを直した。 | FeatureBench | standards_tools | primary | fact |  | verified | [FeatureBench の README](https://raw.githubusercontent.com/LiberCoders/FeatureBench/main/README.md) |

## 抜粋

- C1：「SWE-EVO comprises 48 release-sized tasks requiring multi-step modifications spanning an average of 21 files, validated against test suites averaging 874 tests per instance.」
- C2：「the best model reaches only 25% on SWE-EVO」
- C3：「while gpt-5.2 drops from 72.80% on SWE-Bench Verified to 22.92% on SWE-EVO」
- C4：「For the GPT series, gpt-5 rarely fails due to syntax or tool issues; instead, over 60% of failures come from Instruction Following」
- C5：「Because SWE-EVO contains 48 curated instances, one resolved instance changes Resolved Rate by 2.08 percentage points.」
- C6：「the benchmark has 48 curated instances and an imbalanced repository distribution, especially toward dvc」
- C7：「the benchmark shows signs of saturation on isolated tasks.」
- C8：「python SWE-bench/evaluate_instance.py」
- C9：「we curated 200 challenging evaluation tasks and 3825 executable environments from 24 open-source repositories in the first version of our benchmark.」
- C10：「the state-of-the-art agentic model, such as Claude 4.5 Opus, which achieves a 74.4% resolved rate on SWE-bench, succeeds on only 11.0% of tasks」
- C11：「both task success rates and pass rates increased significantly」
- C12：「the dominance of NameError suggests that current LLMs still struggle with cross-file dependency resolution.」
- C13：「they often resort to guessing (even hallucinating) the interface or attributes of components defined across files, rather than performing the actual file reading required to retrieve precise prototypes and members.」
- C14：「Regarding token consumption, all LLMs consume over one million input tokens.」
- C15：「Increasing the maximum step size from 50 to 100 results in notable performance gains for both Gemini-3-Pro-Preview and Qwen3-Coder-480B-A35B-Instruct. However, beyond this threshold, the improvements become marginal.」
- C16：「These instances require no GPU and are optimized for rapid evaluation.」
- C17：「Gemini-3-Pro-Preview ／ Gemini-CLI ／ 43.4 ／ 10」
- C18：「primarily correcting inconsistencies and omissions in task problem statements.」

## 確かめられなかったこと

- 自作の鎖の課題の数（素の A が 6/6 で完了・ゲートは 42 ステージで 1 回目に合格）は手元の実測（docs/v4/experiments.md の M-03）で、出典の主張ではない。
- SWE-EVO は学習データへの混入（同じリポジトリと公開の次の版）を論じていない。どちらの腕にも同じに効くが、差を縮めうる。
- SWE-EVO の失敗の分けは LLM の判定（gpt-5-mini）で、人との一致は確かめられていない（論文の §4.3.1）。
- FeatureBench のイメージの大きさは、Docker Hub の問い合わせの上限で数えられなかった。SWE-EVO の 42 個は圧縮で計 70.9GB（中央 1.37GB）。
- 手元の agy（Gemini 3.8 Flash）で、どちらのベンチの課題がどれだけ解けるかは、まだ測っていない（校正の 1 回目で測る）。
- 反証の段（Argon）はまだ行っていない。

## 検索語

- SWE-EVO benchmark long-horizon software evolution arXiv 2512.18470
- FeatureBench agentic coding feature development benchmark ICLR 2026
- mini-SWE-agent bash only scaffold matches complex agent scaffolds SWE-bench Verified results

## 問い

# 公式の長い課題のベンチの比較：SWE-EVO と FeatureBench（と自作の鎖の課題）

## 段
compare

## 決めたい判断
v4 の比べの土俵（素の A の校正・ハーネスの開発・held-out の証明）に、どのベンチをどの順で使うか

## 判断を変えうるもの
素のエージェントがほぼ全部解く。手元の環境（Windows と WSL の Docker）で動かない。1 課題の費用が個人の枠に収まらない。第三者の信用が無い。課題の数が少なすぎて差が見えない

## 候補
- SWE-EVO（版から版へのリリースの差分、48 課題）
- FeatureBench（機能の単位、Lite 30・Fast 100・Full 200）
- 自作の MuJoCo の鎖の課題（v3 の E4・E5 で使ったもの）

## 問い
それぞれの課題の規模・難しさ・素のエージェントの失敗の型・評価の基盤・保守・費用と、ハーネスの仮説（入口の台帳・出口の検証）との合い方はどうか。
