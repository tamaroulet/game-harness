# game-harness

ハーネス自身を、人が起草した What から自律で改修していく開発ハーネス。
今の主役は **H ライン**（受信箱の What → 実装役 → Gate 1 → 統合ブランチ → 統合 PR）と、その外側の周期、工廠とベンチ、進捗の台帳。
ゲームを作る前の経路（falling-blocks・unity-2d）は、末尾の「前の経路」に分けて残してある。

移設元: `tamaroulet/unity-2d` の `tools/`（`c06194a` 時点）。それ以前の経緯は unity-2d の git 履歴にある。

## 役割の分担

| 席 | 場所 | 何をするか |
|:--|:--|:--|
| 人間 | 統合 PR のレビュー | 統合 PR を読んで承認する。承認のラベルを付けられるのは人間だけ |
| 総監督 | Claude CLI（`harness/templates/director_room/`、部屋は `python -m harness.hline setup` が書く） | 受信箱に What を 1 件 1 ファイルで起草する。コードの細部は見ない |
| H ライン | `harness/hline.py` と `hline_*.py` | 受信箱の What を取り、実装役を呼び、Gate 1 を通った変更を統合ブランチに積み、統合 PR を 1 本出す |
| 実装役 | `config/hline.json` の `implementer`（CLI とモデルを固定） | 作業ツリーの中だけで編集する。テストは走らせない |
| 監修役 | `config/hline.json` の `reviewer`（`harness/hline_review.py`） | 取り込みの段で What を読む。合否は決めない |
| Gate 1 | `harness/hline_gate.py` | LLM を使わない。影響テストと `gate_always_modules` を走らせ、保護パス `config/protected_paths.json` を守る |
| 外側の周期 | `hline/outer` の週次の統合 PR | 基盤の改善を週に 1 本の PR にまとめる |
| 工廠とベンチ | `harness/planner.py`、`harness/bench_run.py` | 課題から計画を作り、凍結の 10 本で工廠の点を測る |
| 台帳 | `docs/progress.yaml`、`python -m harness.progress` | 何が終わったかを、検証コマンドの終了コードだけで記録する |

### H ライン

- 受信箱（`config/hline.json` の `inbox`）の What を名前の順に 1 件ずつ取り、依存の順に統合ブランチ（`hline/integration`）へ積む。`python -m harness.hline poll` をタスク スケジューラが 10 分ごとに呼ぶ
- 実装役の CLI・モデル・ターン数は `config/hline.json` の `implementer`。モデルは正確な ID で固定し、実行のたびに CLI が報告したモデルと照合する（`harness/model_pin.py`、`python -m harness.model_pin`）
- 収束しなければ未収束として記録し、次の What へ進む。インフラの例外は `harness/infra_retry.py` が別に数える
- 統合 PR の前に、生ログ・一時ファイル・大きすぎるファイルが混ざっていないかを `harness/hline_hygiene.py` が見る
- 承認済みで CI が緑なら `harness/hline_merge.py` がマージする。ラベルは付けも外しもしない

### Gate 1

- 試行のあと、変更の影響テスト（`harness/impacted.py`）と、`config/hline.json` の `gate_always_modules` を、`python -m harness.fastsuite` がモジュールごとに別のプロセスで並列に走らせる
- 判定は終了コードだけ。落ちたテストの名前とトレースバックは次の試行に渡す（`harness/gate_order.py`）
- 保護パス（`config/protected_paths.json`、`harness/hline_protect.py`）と基盤のパス（`config/foundation_paths.json`、`harness/hline_foundation.py`）を変える変更は通さない

### 外側の周期

- マイルストーン outer の What は `hline/outer` に積み、統合 PR は ISO 週に 1 本（`harness/hline_outer.py`）
- 非劣性の検査は `harness/bench_regress.py`。凍結の 10 本の合計の率が基準より下がっていないかを決める
- 週次の報告は `harness/weekly_report.py`、周期の集計は `harness/outer_cycles.py`

### 工廠とベンチ

- 計画役 `harness/planner.py`（`harness/plan_loop.py`、`harness/plan_check.py`）が課題から What と台帳を作る
- 走らせ役は `harness/bench_run.py`。ベンチの実装役の設定は `config/bench_implementer_agy.json`
- 凍結の 10 本の計画は `bench/plans/` に、記録は `bench/records/` に置く

### 進捗の台帳

`docs/progress.yaml` は手で編集しない。状態を変えるのは `python -m harness.progress complete <id>` だけで、検証コマンドが期待した終了コードを返したときだけ完了になる。現在地は `python -m harness.progress anchor`、報告は `python -m harness.progress report`。

## 使い方

```
python -m harness.hline setup
python -m harness.hline poll
python -m harness.fastsuite discover -s tests
python -m harness.progress report
python -m harness.model_pin
python -m unittest discover -s tests
```

## 構成（今の経路）

```
harness/
  hline.py          H ライン本体への入口（poll / setup）。同名の関数は host を渡す薄い委譲
  hline_base.py     設定・子プロセス・エージェントの呼び出し・ロック
  hline_queue.py    受信箱の取り込み・キュー・台帳の依存
  hline_task.py     1 件の What の実行（試行の繰り返し）
  hline_prompt.py   実装役への指示の組み立て
  hline_gate.py     Gate 1
  hline_git.py      統合ブランチ・作業ツリー・統合 PR
  hline_outer.py    外側の周期（週次の統合 PR）
  hline_report.py   report.md・TODO.md・PR 本文
  fastsuite.py      全件テストをモジュールごとに並列に走らせる
  bench_run.py      工廠の走らせ役
  bench_regress.py  非劣性の検査
  planner.py        計画役
  progress.py       進捗の台帳
config/             設定（hline.json・protected_paths.json・foundation_paths.json ほか）
docs/               設計（docs/design/）・指示（docs/prompts/）・台帳（docs/progress.yaml）
bench/              凍結の 10 本の計画と記録
tests/              unittest。tests/test_readme_paths.py が、この README に書いたパスの実在を確かめる
```

## 前の経路（falling-blocks・unity-2d）

H ライン以前の、ゲームのリポジトリを相手にした経路。モジュールはまだあるが、今の H ラインの経路には入っていない。主役ではない。

| 席 | 場所 | 何をするか |
|:--|:--|:--|
| 分解役 | Claude CLI（`harness/decompose.py`） | 要求を受入テストと単位定義にする |
| 実装役 | agy（`harness/pipeline.py` が呼ぶ） | サンドボックスで実装する。テストには触れない |
| 門番 | `harness/pipeline.py` / CI | 機械で判定する |
| 運び役 | `harness/scheduler.py` | 上をつなぎ、Issue をマージまで運ぶ |

```
harness/
  project.py      プロジェクト設定の読み込み
  scheduler.py    ready の Issue → 分解 → 実装 → 統合ブランチ → 統合 PR
  pipeline.py     1 単位を実装して門を通す（終了コード 0 / 1 / 2）
  decompose.py    Issue → 受入テスト + 単位定義
  oracle.py       二相判定（F2P / P2P）・制御群・quarantine
  playtest.py     プレイ確認用のビルド
  telemetry.py    実測値の記録（docs/design/telemetry.md）
  proc.py         子プロセスの起動（TTL 必須）
  exitcode.py     未捕捉例外を rc=2 にそろえる
  fileops.py      ファイル操作の再試行（Windows のファイルロック対策）
  adapters/       エンジン・言語に固有の処理（unity.py・dotnet.py・csharp_tests.py）
  templates/game-repo/   ゲームのリポジトリに配るもの（承認の必須チェック）
projects/<id>/    project.json・pipeline.json（falling-blocks・unity-2d）
```

```
python harness/scheduler.py --project unity-2d --dry-run
python harness/pipeline.py  --project unity-2d --unit tools/units/metapoint.json --selftest
python harness/decompose.py --project unity-2d --issue 12
python tests/mutate.py
```

- 統合 PR は 1 本だけで、人間の承認を 1 回受ける（`docs/design/spec_pipeline.md`）
- 競合したら自分では直さず、取り消して ABORT する
- 終了コードは 0 = 成功、1 = 門番の不合格、2 = 環境異常（ABORT）で共通

## 正直に書いておくこと

- **サンドボックスの隔離は機構ではない。** 実装役は同じ OS ユーザーで動く。破られたことを検出して止めるところまで
- **承認者と H ラインは同じ GitHub アカウントで動きうる。** 承認のラベルは人間だけが付ける取り決めで、機構で区別してはいない
- **前の経路の Unity の受入はこの PC でしか走らない。** GitHub の必須チェックは Pure C# の `dotnet test` だけ
