# 不変条件（invariants）

1 行 1 件。「強制場所」は、それを機械で守っているファイル。状態は「強制」「設定のみ」（設定に書かれているが実装との照合は未確認）「未強制」「未確認」。
パスは `C:\src\game-harness\` から（受信箱のものは明記）。いまの `main` のコミットは report.md の probe の節で見る（D-025）。

（2026-10-24：W-169 の表 `docs/design/rules_inventory.md`（game-harness#286）で「消す」とした行（U-08・U-10・U-11・U-13、暫定の値の棚卸しの表、運用の節）を消した版。行番号は 10-11〜10-16 のもので、今のコードとは表の側で照らし直してある）

## 強制されているもの

| ID | 不変条件 | 強制場所 | 状態 |
|---|---|---|---|
| I-01 | 実装役は保護パスを変えられない（一覧自身と守りのテストを含む） | `config/protected_paths.json:3-10`、`harness/hline_protect.py:13`（必須項目） | 強制 |
| I-02 | `docs/progress.yaml` について。実装役は変えられない。H ラインが変えるのは `harness.progress complete` だけ。人間の PR は正規形で書き、`validate` を通す。タスクの完了は、origin/main の版にある検証コマンドが通ったときだけ記録される | `config/protected_paths.json:5`、`harness/hline_git.py:79`、`tests/test_progress.py:55-60`、`harness/progress.py:299-306` | 強制 |
| I-03 | main へのマージには人間の承認（`ms4:approved`）が要り、push で失効する | `.github/workflows/approval.yml:5`、`.github/scripts/ms4_approval_gate.py:13-18` | 強制 |
| I-04 | エージェントは `ms4:approved` を付けない | `docs/design/spec_pipeline.md:19`、`config/scheduler.json:28` | 強制（スケジューラ）／運用（その他） |
| I-05 | 総監督はシェル不可・受信箱の外に書けない・生ログと作業ツリーを読めない | 受信箱 `.claude/settings.json:3-31`（生成：`harness/hline_room.py:13`） | 強制 |
| I-06 | 実装役はテストを実行できない（Read・Grep・Glob・Edit・Write だけ） | `config/hline.json:64-69` | 設定のみ |
| I-07 | 実装役のモデルは固定 | `config/hline.json:59`、`harness/model_pin.py` | 設定のみ |
| I-08 | Gate 1 の判定は終了コードだけ。LLM は使わない | `config/hline.json:35` | 設定のみ |
| I-09 | Gate 1 は影響テスト（無ければカナリア）とタスクの検証コマンドだけを走らせ、リポジトリ全体の全件は走らせない。Gate 1 の後・積む前に、変えたパッケージの全件を走らせ、落ちたら積まない（G0-2。旧 U-04） | `config/hline.json:35-42`、`harness/gate_order.py:49-69`、`tests/test_hline_full_before_stack.py` | 強制（全件の段は G0-2、#168） |
| I-13 | 統合 PR の自動マージは、ワークフローごとに最新の走行だけで判定する。承認（`ms4:approved` と `approval` の緑）と `harness-tests` の緑がそろったときだけ H ラインがマージし、ラベルは付け外ししない（旧 U-12） | `harness/hline_merge.py`、`tests/test_hline_auto_merge.py`、`tests/test_hline_latest_check_run.py` | 強制（#169・#170・#172 をラベル 1 回で H ラインがマージ） |
| I-14 | タスクを宣言しない What は `検証:` が必須で、Gate 1 がそのコマンドを影響テストと一緒に走らせる。タスクを宣言した What は `検証:` を書かない（台帳が正本）。どちらかに反する What は取られず受信箱に残る | `tests/test_hline_what_verification.py`、雛形 `harness/templates/director_room/CLAUDE.md:31-43` | 強制（#172）。受信箱の `CLAUDE.md` への写しは `python -m harness.hline setup` を走らせたときだけ |
| I-15 | Gate 1 の検証コマンドは shell を通さずに走る（`&&` や `|` は使えない）。`harness.progress complete` は shell で走る（`&&` が使える）。H ラインのタスクの検証コマンドは 1 つの命令に限る | `harness/impacted.py:136-143`（`shlex.split`）、`harness/progress.py:377`（`shell=True`） | 強制（コードの事実） |
| I-10 | 1 つの What の実装役の試行は最大 2 回。収束しなければ未収束にして次へ | `config/hline.json:16-18` | 設定のみ |
| I-11 | 統合 PR に生ログ・キュー・report 等を混ぜない | `config/hline.json:79-92` | 設定のみ |
| I-12 | タスクごとの PR は作らず、積むものが尽きたら統合 PR を 1 本だけ出す | `config/hline.json:10` | 設定のみ |

## 未強制・未確認のもの（ここに載っている限り、守られている前提で計画しない）

| ID | 不変条件 | 現状 | 予定 |
|---|---|---|---|
| U-01 | 規模の制約（追加 300 行・新規モジュール 500 行・複雑度 15） | **門ではなく警告**（10-11 に照合）：Gate 1 は規模の問題を不合格にせず warnings に積む（`harness/hline_gate.py:13-14`、`tests/test_size_limits.py:81-97`）。What の大きさの実際の天井は実装役 1 回 15 ターン（`config/hline.json:75`） | 門にはしない（DR-004 改訂 2） |
| U-02 | 同じマイルストーンで凍結が 2 件に達したらラインを止める | **実装されていない**（10-11 に照合）：設計は `docs/design/foundation_v3_review.md:94`、キューは未収束の下流を凍結するだけ（`harness/hline_queue.py:98-117`）。設計の自動の再分解 1 回（同 `:91`）も、設定は `respecs: 0`（`config/hline.json:17-18`） | 作らない（DR-004 改訂 1 の W-13・W-14 は D-052 で外した） |
| U-03 | 判定は内部状態の証跡で行う（標準出力の照合では行わない） | 未強制 | G1-3（decisions.md D-011） |
| U-05 | 要件 ID の網羅率 | 道具はある（G0-3：`harness/reqcov.py`）。Gate 1 には入っていない。照合は `checks/c2-reqcov.json`（試験台だけ） | 段 3 の課題の仕様の検査で使う（W-20 の PR の確かめ：`harness.reqcov --spec docs/bench/B06/spec.md --list`） |
| U-06 | CI の依存の版の固定（mujoco） | 固定済み（`.github/workflows/harness-tests.yml:25`、`mujoco==3.15.0`）。CI の外（ローカル）の版の照合は無い | 強制欄へ移すかは次の docs PR で |
| U-09 | 実行の順は `depends_on` の DAG（D-041）。ID の不変と slug の形は運用（W-2・W-3 は凍結：D-048） | DAG は強制（W-1 で済み：`harness/progress.py:81-121` の検査・現在地の選び方・完了の拒否）。ID の不変と slug の形は運用のまま。依存の無い待ちの What は名前の順に取られる（`harness/hline_queue.py:187`） | 順序が要る What は `依存:` か台帳の `depends_on` で書く（名前の順に頼らない） |
| U-07 | H ラインのタスクに検証コマンドがある | 検証コマンドが見つからないタスクは、Gate 1 が影響テストだけで判定し警告に積む（`harness/gate_order.py:56-59`）。タスクの無い What は I-14 で `検証:` が必須 | 台帳のタスクを足す PR で、検証コマンドを同時に入れる |
