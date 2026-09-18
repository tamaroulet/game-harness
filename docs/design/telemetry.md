# テレメトリと査読用の指標（Step 4）

- 置き場所: `<out_dir>/runs.jsonl`（`projects/<id>/project.json` の `out_dir`。リポジトリの外）
- 1 行 = scheduler が 1 つの Issue / PR を処理した記録。不合格・ABORT の行も残す（生存者バイアスを作らない）
- 判定には使わない。**テレメトリの失敗で門・承認・マージを止めない**

## 1. null の規則

- 取れない値は **0 にしない**。`null` にして、同じ階層に `<key>_null_reason` を置く
- 報告された 0 は 0 のまま（例: agy の `cache_read_tokens: 0`）
- 和は、要素が 1 つでも不明なら不明にする（少なく数えない）

## 2. 誰が何を書くか

| 道具 | 書き出し | 中身 |
|:--|:--|:--|
| `decompose.py --telemetry` | ログの隣の `decompose.telemetry.json` | 分解役（claude）の利用量、秒数 |
| `pipeline.py --telemetry` | `pipeline.telemetry.json` | ハーネスと base の SHA、自己検査、base、**全試行**（落ちた門・理由・秒数・フィードバックの長さ・実装役の利用量・差分のハッシュ・P2P 破壊件数） |
| `pipeline.py`（常時） | テレメトリの隣の `implementer_attempt_<n>.log` | 実装役に渡したプロンプトと、返ってきた stdout / stderr の**生のまま**。`--telemetry` が無ければ `<out_dir>/pipeline/` に落ちる |
| `scheduler.py` | `runs.jsonl` | 上の 2 つを `steps[].telemetry` に取り込み、Issue 単位の指標を計算する |

監査役（`audit.py`）はテレメトリを書かない（`telemetry: null`）。トークンの合計にも含めない。

`implementer_attempt_<n>.log` は **rc が 0 でも書く**。実装役が「なぜ実装を書かなかったか」を
書くのは応答本文だけで、終了コードにも差分にも出ないため。2026-09-18 の Issue #12 は 3 試行とも
rc=0・差分ゼロで不合格になったが、当時は rc=0 の応答を捨てていて、実行記録からは理由を追えなかった。
判定には使わない。書けなくても実行は止めない。

## 3. CLI の利用量（2026-09-17 に 1 回ずつ実測）

| 項目 | agy（`--output-format json`） | claude（`--output-format json`） |
|:--|:--|:--|
| 本文 | `response` | `result` |
| 入力 | `usage.input_tokens` | `usage.input_tokens` |
| 出力 | `usage.output_tokens` | `usage.output_tokens` |
| キャッシュ読み | `usage.cache_read_tokens` | `usage.cache_read_input_tokens` |
| キャッシュ作成 | 報告しない → null | `usage.cache_creation_input_tokens` |
| 合計 `total_tokens` | `usage.total_tokens`（**CLI の定義のまま**） | 入力 + キャッシュ作成 + キャッシュ読み + 出力 |
| コスト | 報告しない → null | `total_cost_usd` |

**未確認**: agy の `total_tokens` がキャッシュ読みを含むかは、実測でキャッシュ読みが 0 だったので分からない（13582 + 111 = 13693 で、入力 + 出力には一致した）。比較で両 CLI の `total_tokens` を並べるときは、この違いを明記する。

## 4. 査読用の 4 指標

| キー | 行 | 定義 |
|:--|:--|:--|
| `p2p_violation_rate`（%） | AWAITING / REJECT | 受入判定まで到達した試行のうち、P2P 破壊が 1 件以上あった試行の割合 × 100。分母は `attempts_judged`、各試行の `p2p_broken` / `p2p_base` も残す。到達した試行が 0 なら null |
| `token_to_accepted_loc`（tokens/行） | PASSED | `tokens_total / accepted_loc`。`tokens_total` は、その Issue の runs.jsonl の全行（不合格の回を含む）にある、分解役と実装役の全呼び出しの `total_tokens` の和。`accepted_loc` はマージコミットの第 1 親からの追加行（`test_dir`・単位定義・監査レポート・`reports/` を除く） |
| `human_active_intervention_time`（分） | 承認・却下を処理した行 | `human.active_seconds / 60`。**Step 4 では常に null**（dispatch 未計測）。代わりに `human.wait_seconds`（最後の `ms4:awaiting-approval` 付与から、最初の承認・却下ラベルまで）と `human.decided_by` を残す。待ち時間は操作時間ではない（人間が別の作業をしていた時間を含む） |
| `retry_entropy`（0〜1） | AWAITING / REJECT | 連続する 2 試行の「追加行の集合」の非類似度 `1 − |A∩B| / |A∪B|` の平均。0 は同じ差分の繰り返し、1 は全面的に別の差分。追加行は前後の空白を除き、空行を捨てる。差分のある試行が 2 回未満なら null。あわせて `retry_same_failure_repeats`（連続する不合格で、落ちた門と理由のハッシュが同じだった回数）を残す |

どの行にも 4 つのキーは必ずある。その行の種類で算出しないものは、null と理由（「PASSED の行でだけ算出する」など）になる。

### `retry_entropy` の名前について

名前は entropy だが、**Shannon エントロピーではない**。差分のハッシュの分布でエントロピーを取ると、LLM の出力はほぼ毎回ハッシュが異なるので、値が常に log2(n) に張り付いて何も測れない。
堂々巡り（同じような修正の繰り返し）を見たいので、連続する試行の差分の重なりで定義した。論文では定義式を併記する。

## 5. 比較実験の条件

- `condition`: `config/scheduler.json` の `experiment_condition`（今は `"harness"`）
- 比較対象の「門番の無い再試行ループ」は、同じ形の行を `condition: "loop-no-gate"` で書く（その実行系はまだ無い）
- `harness_sha`（ハーネスの版）と、pipeline のテレメトリにある `unit_sha256`・`implementer.model_name` で、どの規則・どのモデルで得た値かを復元できるようにする
