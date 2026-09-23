# game-harness の作業規約

## 進捗（docs/progress.yaml）

- 毎ターン、考え始める前に `python -m harness.progress anchor` を実行し、現在地・検証コマンド・制約を確かめる
- チャット報告は `python -m harness.progress report` の出力をそのまま貼ることのみを許可する。前後に挨拶、解説、変えた点、手順、言い訳などの自然言語を作文することを一切禁止する。詳細・記録はすべて GitHub の PR 本文に書くこと
- PR を出して人間のレビューを待つときは `python -m harness.progress review <task_id> <PR の URL>` で状態に記録する（report の「人間作業」が REVIEW_REQUIRED になる）
- `docs/progress.yaml` を手で編集しない。状態を変えるのは `python -m harness.progress complete <id>` だけで、検証コマンドが期待した終了コードを返したときだけ完了になる
- 検証コマンドを足したり変えたりするときは、main への PR で行う（complete は origin/main の版と照合する）

## 表記

- Issue と PR はリポジトリ付きで書く（falling-blocks#15、game-harness#44）。「Issue 15」「PR 24」とは書かない
- 段は B3.1〜B3.3 のように全体ロードマップの番号で書く
- 件数は数えてから書く。推測の件数を出さない

## 視界（防壁①）

- `falling-blocks` / `unity-2d` の `Game/Assets/Core/**`・`tests/**`、C# のテストコード、`dotnet test` の生ログを見ない
- ゲームのリポジトリへの書き込みは、ハーネスのコマンド（`canary.py migrate` など）を通して行う
