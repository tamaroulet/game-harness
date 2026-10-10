# 不変条件（invariants）

1 行 1 件。「強制場所」は、それを機械で守っているファイル。状態は「強制」「設定のみ」（設定に書かれているが実装との照合は未確認）「未強制」「未確認」。
パスは `C:\src\game-harness\` から（受信箱のものは明記）。2026-10-10 時点、`main` = `2f47fb8`（操縦士の実測）。

## 強制されているもの

| ID | 不変条件 | 強制場所 | 状態 |
|---|---|---|---|
| I-01 | 実装役は保護パスを変えられない（一覧自身と守りのテストを含む） | `config/protected_paths.json:3-10`、`harness/hline_protect.py:13`（必須項目） | 強制 |
| I-02 | `docs/progress.yaml` を変えるのは `harness.progress` だけ | `docs/progress.yaml:5`、I-01 の一覧 | 強制 |
| I-03 | main へのマージには人間の承認（`ms4:approved`）が要り、push で失効する | `.github/workflows/approval.yml:5`、`.github/scripts/ms4_approval_gate.py:13-18` | 強制 |
| I-04 | エージェントは `ms4:approved` を付けない | `docs/design/spec_pipeline.md:19`、`config/scheduler.json:28` | 強制（スケジューラ）／運用（その他） |
| I-05 | 総監督はシェル不可・受信箱の外に書けない・生ログと作業ツリーを読めない | 受信箱 `.claude/settings.json:3-31`（生成：`harness/hline_room.py:13`） | 強制 |
| I-06 | 実装役はテストを実行できない（Read・Grep・Glob・Edit・Write だけ） | `config/hline.json:64-69` | 設定のみ |
| I-07 | 実装役のモデルは固定 | `config/hline.json:59`、`harness/model_pin.py` | 設定のみ |
| I-08 | Gate 1 の判定は終了コードだけ。LLM は使わない | `config/hline.json:35` | 設定のみ |
| I-09 | 内側のループでは全件テストを走らせない（影響テスト、無ければカナリア） | `config/hline.json:35-42` | 設定のみ |
| I-10 | 1 つの What の実装役の試行は最大 2 回。収束しなければ未収束にして次へ | `config/hline.json:16-18` | 設定のみ |
| I-11 | 統合 PR に生ログ・キュー・report 等を混ぜない | `config/hline.json:79-92` | 設定のみ |
| I-12 | タスクごとの PR は作らず、積むものが尽きたら統合 PR を 1 本だけ出す | `config/hline.json:10` | 設定のみ |

## 未強制・未確認のもの（ここに載っている限り、守られている前提で計画しない）

| ID | 不変条件 | 現状 | 予定 |
|---|---|---|---|
| U-01 | 規模の制約（追加 300 行・新規モジュール 500 行・複雑度 15） | 値は `tests/test_size_limits.py:54`。門として働いているか警告に留まるかは未確認（一次情報で未照合） | H0 の後に確認 |
| U-02 | 同じマイルストーンで凍結が 2 件に達したらラインを止める | `docs/design/foundation_v3_review.md:94`。実装の照合は未確認 | 同上 |
| U-03 | 判定は内部状態の証跡で行う（標準出力の照合では行わない） | 未強制 | G1-3（decisions.md D-011） |
| U-04 | What ごとに積む前に L1 全件テスト | 未強制 | G0-2 |
| U-05 | 要件 ID の網羅率 | 未強制 | G0-3 |
| U-06 | CI の依存の版の固定（mujoco） | 固定済み（`.github/workflows/harness-tests.yml:25`、`mujoco==3.15.0`）。CI の外（ローカル）の版の照合は無い | 強制欄へ移すかは次の docs PR で |
| U-07 | G0 の各タスクに検証コマンドがある | `docs/progress.yaml:626-661` で全件 `verification: null` | 大局ロードマップで設計 |

## 運用

- 新しい不変条件は、強制場所が決まってから「強制」に載せる。文章だけのものは「未強制」に置く
- 「設定のみ」を「強制」に上げるには、それを確かめるテストのパスを強制場所に足す
