# game-harness

ゲームを何本でも同じ道具で作るための、自律開発ハーネス。
スケジューラ・実装パイプライン・分解役・監査役を **1 本だけ** ここに置き、ゲームごとの違いは `projects/<id>/` の設定に閉じる。

移設元: `tamaroulet/unity-2d` の `tools/`（`c06194a` 時点）。それ以前の経緯は unity-2d の git 履歴と `reports/TIMELINE.md` にある。

## 役割の分担

| 席 | 場所 | 何をするか |
|:--|:--|:--|
| 人間 | `C:\src\dispatch` だけ | 要求・GDD を書いて送る。承認する |
| 分解役 | Claude CLI（`harness/decompose.py`） | 要求を受入テストと単位定義にする。テストを先に書く |
| 監査役 | OpenAI API（`harness/audit.py`） | テストを書いていない第三者として盲点を挙げる |
| 実装役 | agy（`harness/pipeline.py` が呼ぶ） | サンドボックスで実装する。テストには触れない |
| 門番 | `harness/pipeline.py` / CI | 機械で判定する。人間を呼ばない |
| 運び役 | `harness/scheduler.py` | 上をつなぎ、Issue をマージまで運ぶ |

## 構成

```
harness/
  project.py      プロジェクト設定の読み込み（全道具がここを通す）
  scheduler.py    ready の Issue → 分解 → 監査 → 実装 → PR → 承認 → マージ
  pipeline.py     1 単位を実装して門を通す（終了コード 0 / 1 / 2）
  oracle.py       二相判定（F2P / P2P）・制御群・quarantine の検証。テスト名単位で判定する
  playtest.py     プレイ確認（H2）用のビルド。PR の head SHA から専用ワークツリーで実行ファイルを作る
  fileops.py      ファイルの削除・置き換えの再試行（Windows のファイルロック対策）
  telemetry.py    runs.jsonl に載せる実測値（CLI の利用量・試行の指標）。取れない値は null ＋理由（docs/design/telemetry.md）
  decompose.py    Issue → 受入テスト + 単位定義
  audit.py        独立監査
  proc.py         子プロセスの起動（TTL 必須・窓を出さない）
  exitcode.py     sys.exit("ABORT") と未捕捉例外を rc=2 にそろえる
  adapters/       エンジン・言語に固有の処理はここにだけ置く（コアに固有の語が無いことをテストで確認）
    unity.py         Editor のバッチ実行、NUnit3 結果、.meta の GUID 保護
    dotnet.py        dotnet test、TRX、C# のスタブ・テストの探し方
    csharp_tests.py  受入テストの一覧（名前・期待値・メッセージ）を C# から機械抽出
  templates/game-repo/   ゲームのリポジトリに配るもの
    .github/workflows/approval.yml       必須チェック approval（pull_request_target）
    .github/scripts/ms4_approval_gate.py その判定
    .github/ms4-approvers                承認してよい人
config/           全プロジェクト共通の設定
projects/<id>/
  project.json    リポジトリ・ローカルパス・テスト置き場・必須チェック名（ここにだけ書く）
  pipeline.json   実装パイプラインの設定
tests/
  test_scheduler.py   状態遷移・異常系（git は本物、GitHub は偽物、子はスタブ）
  test_approval.py    抽出器と承認ゲート
  test_adapters.py    アダプタの選択・結果ファイルの読み取り（実物）・コアに固有の語が無いこと
  test_oracle.py      二相判定（偽テスト・入れ替わり・消えたテスト・制御群・quarantine の承認）
  test_playtest.py    プレイ確認のビルド（SHA ごとに 1 回・非破壊の確認・失敗の区別）と分解役の宣言
  test_fileops.py     ファイルロックの再試行（実物の共有違反）・サンドボックスのリセットの確認
  test_telemetry.py   テレメトリ（null と 0 の区別、実測した CLI の JSON、査読用の指標の手計算）
  mutate.py           判定をわざと壊して、テストが赤になるかを確かめる
```

## 承認とマージ

```
門を通過 → PR（Closes #N）→ 受入テスト一覧を「今の head SHA 宛て」にコメント → ms4:awaiting-approval
人間: dispatch --approve <project>#<PR>（一覧を表示して y/n）
必須チェック approval: 承認者が・今の head SHA に対して付けたラベルか（時刻は比べない。push で失効）
スケジューラ: 必須チェックが全部 success → gh pr merge --merge --match-head-commit <承認した SHA>
```

- マージはマージコミット（squash しない。履歴の追跡性のため）
- 承認後に push されたら、マージせず承認待ちに戻す
- 承認は実装が門を通った後に 1 回。実装前に承認させると、実装の push で承認が失効するため。代償として、テストが誤っていても実装を 1 回走らせる

## 使い方

```
python harness/scheduler.py --project unity-2d --dry-run
python harness/scheduler.py --project unity-2d
python harness/pipeline.py  --project unity-2d --unit tools/units/metapoint.json --selftest
python harness/decompose.py --project unity-2d --issue 12
python -m unittest discover -s tests
python tests/mutate.py
```

`--unit` と `--file` は、ゲームのリポジトリからの相対パス。

## プレイ確認（H2）

```
分解役: 単位に playtest: "none" | "required"（見た目・手触り・間に関わるなら required）
スケジューラ: required なら PR に ms4:playtest-required → playtest.py で head SHA から実行ファイルを作る → PR にコメント
人間: dispatch --playtest <project>#<PR>（起動して、確認項目ごとに OK / NG / 保留）→ 結果を SHA 付きで PR にコメント
人間: dispatch --approve は、今の SHA に全項目 OK の結果が無ければ拒否する
```

- ビルドは専用ワークツリー（`paths.playtest_worktree`）で行い、出力は `paths.playtest_out/<project>/pr<N>-<sha8>/`。同じ SHA は作り直さない
- **非破壊の確認**: ビルド後にワークツリーの追跡中のファイルが変わっていたら rc=2（止める）
- ビルドの失敗（rc=1）は、PR にコメントして承認待ちのまま残す（遊べないので人間が却下する）
- ビルド関数（`pipeline.json` の `playtest_build_method`）はゲーム側の Editor スクリプトが持つ。**unity-2d への配備と実物のビルドは Step 7**

## 常駐

```
python harness/scheduler.py --project unity-2d --watch [--interval 120]   # 1 周 → 待機 → 1 周
python harness/scheduler.py --project unity-2d --status                   # ロック・ハートビート（何も変えない）
python harness/scheduler.py --project unity-2d --stop                     # 停止を依頼（周の区切りか待機中に止まる）
```

- **登録**（ログオン時に pythonw で起動）: `harness/templates/resident/register-task.ps1 -Project unity-2d`。OS に設定が残るので、`-WhatIf` で内容を確かめてから人間が実行する
- **出力**: `<out_dir>/scheduler-YYYYMMDD.log`（pythonw では画面が無いので付け替える）
- **ハートビート**: `<out_dir>/heartbeat.json` を 30 秒ごとに更新する（子を待つ間も）。900 秒更新が止まったら、自己監視が子の木を止めて rc=2 で終わる
- **ロック**: 常駐の間ずっと持つ。pid が終了していて ABORT の記録が無いロックは、次の起動で自動解放する。ABORT の記録があるロックは、人間が理由を見てから消す
- **GitHub API のレート制限**: 残量 0 ならリセットまで待つ（セカンダリ制限は 60→120→240 秒）。1 回の操作の待ちが 3600 秒を超えたら ABORT。常駐では周の前に残量を見て、300 未満なら着手しない
- **ファイルロック（WinError 32 など）**: 削除・置き換えは `harness/fileops.py` で待って再試行する。サンドボックスのリセットは、作業ツリーが空になったことを確かめ、空にならなければ ABORT

## 終了コード

すべての道具で共通。

| rc | 意味 | スケジューラの扱い |
|:--|:--|:--|
| 0 | 成功 | 次へ |
| 1 | 門番の不合格（環境は正常） | その Issue を `ms4:failed` にして次の Issue へ |
| 2 | 環境異常（ABORT） | 停止。作業ツリーに触らず、ロックを残す |

## プロジェクトを足す

`projects/<id>/project.json` と `pipeline.json` を置く。
リポジトリの位置は `project.json` にだけ書く。共通設定や `pipeline.json` に再掲すると、読み込み時に落ちる（片方だけ直して食い違うのを防ぐため）。

## 正直に書いておくこと

- **サンドボックスの隔離は機構ではない。** 実装役は同じ OS ユーザーで動き、本体を書き換えられる（実測）。破られたことを検出して ABORT するところまで
- **Unity の受入はこの PC でしか走らない。** GitHub の必須チェックは Pure C# の `dotnet test` だけ。Unity 受入を通したことは、スケジューラが保証している（GitHub 側では強制されない）
- **承認者とスケジューラは同じ GitHub アカウントで動いている。** スケジューラは `ms4:approved` を付けないように作り、テストで確かめているが、approval チェックは両者を区別できない。区別するには、スケジューラを別アカウント（machine user）のトークンで動かす必要がある
- **ブランチ保護はまだ無い（A4 で設定）。** それまでは必須チェックも「スケジューラが確かめている」だけで、GitHub 側では強制されない。approval.yml もまだゲームのリポジトリに配っていない
- **常駐の仕組み（`--watch`）はあるが、タスクスケジューラへの登録はまだしていない**（OS に設定が残るため、人間の許可を得てから行う）。dispatch の `--inbox` へのハートビート表示と `--resume` も未実装
- **プレイ確認の結果を承認の条件にしているのは dispatch だけ。** GitHub の必須チェック approval は見ていないので、GitHub の画面から承認ラベルを付ければ通ってしまう（同じアカウントで動く限り、どのみち区別できない）
- **自己監視は「ハートビートが止まった」ことしか見ない。** 子プロセス（pipeline など）の中で固まった場合は、子の TTL（`ttl_seconds`）で止まる
