# LLM によるコードレビューの精度の実測：合否に入れる形と、助言だけにする形

- 記録：R-20261023-q3-llm-review-precision
- 調べた日時：2026-10-23T12:17:32+09:00
- 調べ役：claude（claude-haiku-5-5）
- 照合：verified 7・quote_not_found 1・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|
| C1 | 同一セッションで独立性のない複数回のレビューを重ねると、偽陽性が62%増え、再現率は上がらなかったと、ある単著プレプリントが報告している（この論文はその報告を引いている） | fact | 2026-10-06 | verified | [When Does a Second Model Help? Cross-Model Review in LLM Verification (arXiv:2610.01471v2)](https://arxiv.org/html/2610.01471v2) |
| C2 | 別モデルによるクロスモデル・レビューの F1 は、同一モデルの新しいセッションでのレビューと統計的な有意差がなかった。両者が同等だという意味ではないと著者は述べている | fact | 2026-10-06 | verified | [When Does a Second Model Help? Cross-Model Review in LLM Verification (arXiv:2610.01471v2)](https://arxiv.org/html/2610.01471v2) |
| C3 | クロスモデル・レビューと同一モデル・レビューが見つけた誤りの重なり（Jaccard 類似度）は41.2%で、部分的に別の誤りを見つけている | fact | 2026-10-06 | verified | [When Does a Second Model Help? Cross-Model Review in LLM Verification (arXiv:2610.01471v2)](https://arxiv.org/html/2610.01471v2) |
| C4 | 同一モデルのレビュー1回とクロスモデルのレビュー1回の組み合わせは、同一モデルのレビュー2回より多くの埋め込み誤りを捉えた（56.7% 対 42.7%） | fact | 2026-10-06 | verified | [When Does a Second Model Help? Cross-Model Review in LLM Verification (arXiv:2610.01471v2)](https://arxiv.org/html/2610.01471v2) |
| C5 | クロスモデル・レビューの偽陽性のうち Major/Critical と判定されたものは55%で、真陽性の70%より少なかった。重大度で絞ると偽陽性の約45%と真陽性の約30%が落ちるが、この差は有意ではない（Holm 補正 Wilcoxon p = 1.00） | fact | 2026-10-07 | verified | [Comment on a PREreview of "When Does a Second Model Help? Cross-Model Review in LLM Verification"](https://zenodo.org/records/23202123) |
| C6 | Google の ML によるレビューコメント解決の仕組みでは、提示されたコード編集の40〜50%が作成者に適用された。これは運用上の数字で、CI で合否を決める運用ではなく提案として出された | fact | 2023-05-23 | verified | [Resolving code review comments with ML (Google Research blog)](https://research.google/blog/resolving-code-review-comments-with-ml/) |
| C7 | 同じ Google の記事では、オフライン評価として、目標精度50%でコメントの52%に対処できると見積もられている。これは実運用の実測ではなくオフラインの推定である | fact | 2023-05-23 | verified | [Resolving code review comments with ML (Google Research blog)](https://research.google/blog/resolving-code-review-comments-with-ml/) |
| C8 | 精度の要求は、レビューが マージを止められる場合（ゲート）で高く、助言として広く出す場合は再現率が重要になる、と実務者の解説が述べている。ただし実証データではない | opinion | 不明 | quote_not_found | [How Accurate Is AI Code Review? False Positives, Recall, and Trust](https://www.augmentcode.com/guides/ai-code-review-accuracy) |

## 抜粋

- C1：「increasing false positives by 62% without improving recall」
- C2：「a top-tier cross-model reviewer is not significantly different in F1 from same-model review in a fresh session (CCR)」
- C3：「The Jaccard similarity between CCR and XMR-GPT-R is 41.2% (35 of 85 unique errors found by either)」
- C4：「one CCR plus one cross-model review matches more planted errors than two CCR reviews (56.7% vs. 42.7%」
- C5：「For cross-model reviewers, 55% of false positives were labelled Major or Critical, compared with 70% of true positives,」
- C6：「40% to 50% of all previewed suggested edits are applied by code authors.」
- C7：「Offline evaluations indicate that the model addresses 52% of comments with a target precision of 50%.」
- C8：「precision matters most when a reviewer can block a merge, while recall matters more in an advisory stream」

## 確かめられなかったこと

- CI の合否に入れた運用と助言だけの運用を直接比べ、開発者の受け入れ率や無視率を測った査読済みの研究は、今回の検索では見つからなかった
- Google の 2023 年の記事は、ML によるコード編集の提案が合否に入っていたか、助言だけだったかを明記していない。2024 年の ICSE 論文の 7.5% という数字は検索の要約で見ただけで、原文の本文では確認していない
- Coverity の『偽陽性30%超で開発者が無視する』や Google Tricorder の『10%未満』という閾値は二次情報のブログ経由で、元の論文は確認していない
- arXiv:2601.18844（産業界での静的バグ検出の偽陽性削減）の数値と導入形態は取得できなかった（PDF の本文が読めなかった）
- Augment の解説が引く LLMDFA の『適合率 43.48% → 87.10%』（SMT 検証の追加）は二次引用で、原論文を確認できていない
- CodeAnt AI の『3 モデルで2つ以上が一致した指摘だけ残すと偽陽性が約60%減る』は売り手の主張で、方法が公開されていない。vendor_claim として扱うべきだが、本文の引用を確認できなかったため採用していない
- Claude 固有の精度の実測は見つからなかった。2026年の Claude と GPT-5 の比較ブログは小規模で、信頼できないと判断した
- 敵対的な自己批判（reflection）で偽陽性が減ったという実測は見つからなかった。クロスモデル・反復レビュー・多数決・形式検証の結果のみ

## 問い

# LLM によるコードレビューの精度の実測：合否に入れる形と、助言だけにする形

LLM（大規模言語モデル）にプルリクエストのコードレビューをさせたときの、精度（誤検知の率・見逃しの率・開発者が受け入れた指摘の割合）を実測して公開した例を集めてください。

- レビューの結果を CI の合否（マージを止める）に入れた運用と、助言のコメントだけにした運用の、どちらの例か区別してください
- 別系統のモデルに検査させる、新しい文脈で読ませる、反証の段を挟む、などの工夫で誤検知が減ったという実測があれば含めてください
- 論文・企業の技術ブログ・ツールの公式の評価を優先し、売り手の主張は vendor_claim にしてください
