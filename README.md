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
  scheduler.py    ready の Issue → 分解 → 監査 → 実装 → マージ
  pipeline.py     1 単位を実装して門を通す（終了コード 0 / 1 / 2）
  decompose.py    Issue → 受入テスト + 単位定義
  audit.py        独立監査
  exitcode.py     sys.exit("ABORT") と未捕捉例外を rc=2 にそろえる
config/           全プロジェクト共通の設定
projects/<id>/
  project.json    リポジトリ・ローカルパス・テスト置き場（ここにだけ書く）
  pipeline.json   実装パイプラインの設定
tests/
  test_scheduler.py   状態遷移・異常系（git は本物、GitHub は偽物、子はスタブ）
  mutate.py           判定をわざと壊して、テストが赤になるかを確かめる
```

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
- 計画中: PR 経由の自動マージ、承認の必須チェック、ブランチ保護、常駐（`C:\Users\tamar\.claude\plans\glistening-wobbling-dijkstra.md` のフェーズ A2〜A6）
