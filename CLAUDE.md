# game-harness の作業規約

## 進捗（docs/progress.yaml）

- 毎ターン、考え始める前に `python -m harness.progress anchor` を実行し、現在地・検証コマンド・制約を確かめる
- 進捗ツリーを手で書かない。報告の冒頭に置くツリーは `python -m harness.progress tree` の出力をそのまま貼る
- `docs/progress.yaml` を手で編集しない。状態を変えるのは `python -m harness.progress complete <id>` だけで、検証コマンドが期待した終了コードを返したときだけ完了になる
- 検証コマンドを足したり変えたりするときは、main への PR で行う（complete は origin/main の版と照合する）

## 表記

- Issue と PR はリポジトリ付きで書く（falling-blocks#15、game-harness#44）。「Issue 15」「PR 24」とは書かない
- 段は B3.1〜B3.3 のように全体ロードマップの番号で書く
- 件数は数えてから書く。推測の件数を出さない

## 視界（防壁①）

- `falling-blocks` / `unity-2d` の `Game/Assets/Core/**`・`tests/**`、C# のテストコード、`dotnet test` の生ログを見ない
- ゲームのリポジトリへの書き込みは、ハーネスのコマンド（`canary.py migrate` など）を通して行う
