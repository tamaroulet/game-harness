# B4-RUN の公平性・再現性の監査と、指標・集計の改定

- 状態: 監査と改定の仕様。report.py の所要時間と総コスト・損益分岐は実装済み（このファイルと同じ PR）。§4 の driver・pipeline の修正は、§5 の裁定の後に別 PR で入れる
- 前提: B4-E5 の乾式の走行（dry-01、T1 を A・B で 1 回ずつ、game-harness#60）
- 対象: `docs/design/b4_ab_experiment.md`（以下「設計」）、`harness/ab/*.py`、`harness/pipeline.py`、`experiments/b4_ab/templates/*.md`

## 0. dry-01 の実測

| 条件 | 受入 | 試行 | 実装役の呼び出し | 入力トークン | 出力トークン | キャッシュ読み | 秒（タスク） | うち試行の秒 | うち実装役の秒 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| A | 10/10 | 1 | 1 | 224,628 | 24,966 | 未記録（F6） | 184.2 | — | 未記録（F7） |
| B | 10/10 | 1 | 1 | 624,444 | 28,153 | 1,285,060 | 414.9 | 215.8 | 200.0 |

- B の呼び出し回数は pipeline のログで数えた（「内部試行ループ ターン」1 回、「実装AI」1 回）
- B の 414.9 秒のうち、pipeline の試行の外で 199.1 秒を使っている（サンドボックスの用意、基準の確立、測定器）

## 1. 監査の結果

リスクは「本走の結論を変えうるか」で付けた。高 3 件、中 6 件、低 2 件、計 11 件。

### 1.1 試行・合否の対称性

| # | 指摘 | 根拠 | リスク |
|:--|:--|:--|:--|
| F1 | **実装役の呼び出しの予算が違う**。B は 1 試行の中に内側ループ（`MAX_INNER_LOOP_TURNS = 3`）を持ち、`max_retry: 2` と合わせて最大 9 回呼べる。A は最大 3 回（`max_attempts: 3`）。設計 §2-5「同じ試行の予算」に反する | `pipeline.py` の `attempt()`、`projects/falling-blocks/pipeline.json` | 高 |
| F3 | **A の打ち切りの基準が甘い**。A は「T の受入テストが全部通れば止める」（`run_task_a`）。B の内側ループは「高速検査の全件が通るまで」続く（前のタスクのテストの破壊も直させる）。A は P2P の破壊を直す機会を与えられないので、A の P2P の破壊が作為的に増える | `driver.run_task_a`、`pipeline.attempt()` の `failed_names` | 高 |
| F5 | 不変条件の反例は B にだけ渡る（Outer 段の REJECT の本文）。設計 §2-2「不変条件のテストは、どちらにも見せない」と食い違う。反例の提示は B の処置（防壁）そのものなので、条件差として残すのが正しい。直すのは設計の文面 | `pipeline.attempt()` の `invrun.check` | 中 |
| — | 合否の判定そのもの（受入・P2P・不変条件）は、タスクの終わりに同じ測定器（`measure.py`）で同じ時点に測っている。判定の基準と時点は同一 | `driver.run_condition` | 問題なし |

### 1.2 情報とプロンプトの対称性

| # | 指摘 | 根拠 | リスク |
|:--|:--|:--|:--|
| F4 | **初回のプロンプトの文面が 4 点で違う**。(a) DISPUTE_TEST の逃げ道が B にだけある（A は申し立てできず、B は申し立てるとそのタスクを落とす）。(b) テストの置き場所と「テストを書き換えない」が A にだけある。(c) 計画を返すなという指示が B のほうが強い。(d) 終わりの答え方が違う（A は変更したファイルの一覧、B は何をしたか） | `templates/initial.md`、`pipeline.call_implementer` | 中 |
| — | 仕様の中身（単位定義の `prompt`・`interface`・whitelist）は両条件で同じ。A の再試行（落ちたテストの名前と末尾 40 行）と B の内側ループの失敗の本文（末尾 40 行）も粒度はそろっている。B だけがビルド失敗の診断の射影と不変条件の反例を受け取るが、これは B の処置 | `templates/retry.md`、`pipeline.extract_raw_stacktrace` | 問題なし |
| — | A は会話の履歴を持ち、B は試行ごとに新しい会話。これは比べたい条件差そのもの | 設計 §1 | 問題なし |

### 1.3 計測

| # | 指摘 | 根拠 | リスク |
|:--|:--|:--|:--|
| F2 | **B の利用量が内側ループの最後の呼び出ししか残らない**。`call_implementer` が `c.cur["implementer"]` を呼ぶたびに上書きする。2 ターン目以降があると、前のターンのトークンと秒が消えて B が安く見える。dry-01 は 1 ターンなので影響なし | `pipeline.call_implementer` | 高 |
| F6 | ドライバはトークンの入力・出力だけを残し、キャッシュ読みを捨てる（dry-01 の B は 1,285,060）。A 側のキャッシュ読みも記録されない。費用を比べられない | `driver._tokens` | 中 |
| F7 | 秒がタスクの合計 1 本だけ。実装役・防壁（門と準備）・測定の内訳が無いので、B の 199.1 秒の上乗せが処置の費用か、ハーネスの無駄かを分けられない。集計表にも秒の列が無かった（report.py は本 PR で対応） | `driver.run_condition`、`report.py` | 中 |
| F10 | 設計 §2-4「モデル名をテレメトリで確かめ、違えばその回を無効にする」が未実装 | `driver.agy_call` | 低 |
| F11 | A には whitelist が無い（処置どおり）が、差分は `impl_dir` の下しか数えないので、A が外に書いたものが指標に出ない | `common.numstat` | 低 |

### 1.4 実行環境の独立性（再現性）

| # | 指摘 | 根拠 | リスク |
|:--|:--|:--|:--|
| F8 | **順序の偏り**。`run_all` は奇数回に A→B、偶数回に B→A。3 回だと A が 2 回先行する。コンパイラのサーバー（VBCSCompiler）と NuGet の復元が後発を速くしうる（測定器は `/nr:false` で MSBuild のノードを再利用しない）ので、秒の指標が偏る | `driver.run_all` | 中 |
| F9 | **実装役の外部状態が走行をまたいで漏れうる**。agy はグローバルの `~/.gemini/GEMINI.md` に従う（2026-09-18 の記録）。実装役がこれや自分の設定・記憶を書き換えると、後の走行（別の条件を含む）に効く | agy の構成 | 中 |
| — | worktree・枝・出力先は走行 × 条件ごとに別。`bin/`・`obj/` は worktree ごとなので成果物は混ざらない。NuGet のグローバルキャッシュの共有は、中身が版で決まるので結果に効かない（効くのは時間だけ。F8） | `common.paths` | 問題なし |

## 2. Layer 2（事前設計）の費用の帰属

指摘の「B の前提である Layer 2 の費用が隠れている」は、半分正しい。要求文・単位定義・生成テストは **A も使っている**（A の初回テンプレートは単位定義の `prompt`・`interface`・whitelist を埋め、受入テストを作業ツリーに置く）。これは設計 §2-2「同じ情報」を守るための選択で、その結果 Layer 2 の成果物は両条件の共有の投資になっている。

| 事前投資 | 使う条件 | 区分 |
|:--|:--|:--|
| 要求文（`requirements/T*.md`・`common.md`） | A・B | shared |
| 単位定義（分解役 `decompose.py`）と生成テスト（`testgen.py`） | A・B | shared |
| 不変条件の宣言（`projects/falling-blocks/invariants.json`）と反例の生成 | B | B_only |
| 門の設定（`pipeline.json` の予算・whitelist・禁止パターン） | B | B_only |

- いまの A・B の比較が測るのは「同じ仕様を渡したとき、会話を積むか・防壁の下で走るか」の差で、B_only だけが B の上乗せの事前投資になる
- 「単一チャットの自然な運用（仕様の分解もテストも無い）に比べて、Layer 2 まで含めて元が取れるか」を問うなら、第 3 の条件（A0：要求文だけを渡し、生成テストは測定のときだけ置いて見せない）が要る。これは実験の問いを変えるので、§5 の裁定に回す
- **どちらも実測値が無い**。E3 の分解役は `--telemetry` 無しで走らせ、要求文を書いた Claude の費用も記録していない。推測で埋めず、§3.3 の手順で測る

## 3. 指標と集計の改定

### 3.1 所要時間（本 PR で実装）

- タスク × 条件の表に「秒」（走行の中央値）を足した
- 合計の表に「1 走行の秒（中央値）」と「全走行の秒」を足した
- 内訳（F7）は §4 の修正で `metrics.jsonl` に足してから表に出す：`seconds_implementer`、`seconds_harness`（B の門と準備、A は 0）、`seconds_measure`

### 3.2 総コストと損益分岐（本 PR で実装）

1 回の走行を、条件 X（A か B）でタスク 1〜N まで進めたときの総コストを次で定める。

```
c_X(k) = p_in · input_k + p_out · output_k + p_cr · cache_read_k      （タスク k の走行コスト、USD）
F_A    = F_shared
F_B    = F_shared + F_B_only                                           （事前投資）
S_X(N) = F_X + Σ_{k=1..N} c_X(k)                                       （総コスト）
N*     = min { N : S_B(N) ≤ S_A(N) }                                   （損益分岐）
```

- `c_X(k)` は N 回の繰り返しの中央値。秒でも同じ式を使う（p を 1、トークンの代わりに秒）
- F_shared は両辺に同じだけ入るので N* に効かない。効くのは F_B_only と、c_A と c_B の伸びの差だけ
- 実測の 5 タスクで N* が決まらなければ、A を `c_A(k) = a · k^α`（両対数の最小二乗）、B を実測の平均で外挿し、表に「外挿」と明記する。仮説（A の累積が N² に比例）なら α ≈ 1
- ROI は `(S_A(N) − S_B(N)) / F_B_only` で出せるが、F_B_only が未計測のうちは出さない
- 単価と事前投資は `--cost-model <json>` で与える。単価が無ければ節ごと出さない。形は次のとおり

```json
{
  "prices": {"input_per_mtok": null, "output_per_mtok": null, "cache_read_per_mtok": null,
             "cache_read_included_in_input": false, "source": "<単価の出典と日付>"},
  "fixed": {
    "shared": {"usd": null, "seconds": null, "source": "measured: decompose --telemetry（§3.3）"},
    "B_only": {"usd": null, "seconds": null, "source": "measured / estimate"}
  }
}
```

- agy の出力は `cache_read_tokens` を `input_tokens` と別に報告し、`total_tokens` は入力と出力の和（dry-01 の B で 624,444 + 28,153 = 652,597）。キャッシュ読みが入力に含まれるかは agy の文書で確かめてから `cache_read_included_in_input` を決める

### 3.3 事前投資の測り方

1. shared：`decompose.py --file requirements/T<k>.md --id … --telemetry <scratch>` を T1〜T5 について走らせ、トークンと秒を足す。凍結した単位定義は置き換えない（出力は捨てる）。要求文を書いた費用は記録が無いので、`source` に「未計測」と書いて 0 を入れない
2. B_only：不変条件の宣言と門の設定は、既存のプロジェクトの資産の流用で、この実験のためには書いていない。按分の規則が決まるまで「未計測」とする

### 3.4 品質を含めた比較（本走の後）

安いだけで壊れていれば比較にならない。総コストの横に、タスク N の終わりに残った欠陥（P2P の破壊と不変条件の違反の数）を並べる（既存の合計表）。欠陥を費用に換算する式（直すのに要る呼び出しの平均費用 × 欠陥数）は、修理の実測が無いので定めない。

## 4. driver・pipeline の修正案（§5 の裁定の後に別 PR）

門・指標に触れるので、report.py の PR とは分ける。

| # | 修正 | 対象 |
|:--|:--|:--|
| F1 | 予算を「実装役の呼び出し回数」でそろえる。推奨：A の上限を `max_attempts × MAX_INNER_LOOP_TURNS`（= 9）に上げる。両条件の `metrics.jsonl` に `implementer_calls` を足す | `tasks.json`、`driver.run_task_a` |
| F2 | 呼び出しごとの利用量をリストに積む（`c.cur.setdefault("implementer_calls", []).append(...)`）。ドライバはそのリストを合算する | `pipeline.call_implementer`・`record_attempt`、`driver.run_task_b` |
| F3 | A の打ち切りを「高速検査の全件が通る」にする。再試行テンプレートの落ちたテストは、T のものに限らず全部を並べる | `driver.run_task_a`・`failing_of` |
| F4 | 初回のプロンプトを 1 か所で作る。`pipeline.call_implementer` の前置きと本文の組み立てを `build_prompt(unit, workdir)` に切り出し、A もそれを使う。DISPUTE_TEST は §5 の裁定に従って両方に入れるか両方から外す | `pipeline.py`、`templates/initial.md` |
| F5 | 設計 §2-2 を「不変条件のテストはどちらにも見せない。B は反例の行だけを受け取る（処置の一部）」に改める | `b4_ab_experiment.md` |
| F6 | `_tokens` に `cache_read` を足す（`telemetry.cli_usage` の `cache_read_tokens`） | `driver._tokens` |
| F7 | `seconds_implementer`・`seconds_harness`・`seconds_measure` を足す | `driver.run_condition`・`run_task_b` |
| F8 | 各条件の走行の前に `dotnet build-server shutdown` を呼び、両条件を同じ冷えた状態から始める。繰り返しは偶数回（4 回）にする | `driver.run_condition`・`run_all` |
| F9 | 走行の前後で `~/.gemini/GEMINI.md` と agy の設定の sha256 を取り、変わっていたらその走行を無効（ABORT）にする | `driver.run_condition` |
| F10 | 実装役の出力のモデル名を確かめ、マニフェストと違えば無効にする | `driver.agy_call`、`pipeline.call_implementer` |
| F11 | `impl_dir` の外の変更を別に数える（`diff.outside`） | `common.numstat` |

## 5. 裁定が要ること（人間）

1. **呼び出しの予算（F1）**：A を 9 回に上げる（推奨。B の内側ループは「失敗を見せて直させる」で、A の再試行と同じ行為）か、実験のあいだ B の内側ループを 1 ターンにするか
2. **DISPUTE_TEST（F4）**：両方に入れる（申し立てたタスクは両条件とも不合格として次へ進む）か、両方から外すか。推奨は両方に入れる（B の実運用の姿を変えないため）
3. **A0 の条件（§2）**：Layer 2 まで含めた投資の回収を問うなら、要求文だけを渡す A0 を足す。足すと走行が 1.5 倍になる
4. **単価の出典（§3.2）**：gemini-3.8-flash-high の単価と、キャッシュ読みの扱い
5. **繰り返しの回数（F8）**：3 回から 4 回に増やすか、3 回のまま秒の指標を参考値にするか
