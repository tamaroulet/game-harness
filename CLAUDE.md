# game-harness の作業規約

## 進捗（docs/progress.yaml）

- チャット報告は、ふつうの言葉で、何が済んで何が変わったかと、次の一手（操縦士がすること・会話の席がすること）を書く。ID は説明の補いとして添える。落ちた試験があれば、試験の名前ごとに、ファイル:行と、失敗の 1 行（AssertionError などの文）を書き、変えたファイルがあれば、ファイルごとに 1 行の要約を書いてよい。挨拶・反省・言い訳・手順の作文は書かない。それ以外の詳細と記録は GitHub の PR 本文に書く。高リスク（Tier 2）の PR を出すときは、PR 本文の冒頭に `python -m harness.pilot_brief <PR 番号>` の 5 行の要約の出力を貼る（低リスク時は要約不要）
- PR を出して人間のレビューを待つときは `python -m harness.progress review <task_id> <PR の URL>` で状態に記録する（report の「人間作業」が REVIEW_REQUIRED になる）
- `docs/progress.yaml` を手で編集しない。状態を変えるのは `python -m harness.progress complete <id>` だけで、検証コマンドが期待した終了コードを返したときだけ完了になる
- 検証コマンドを足したり変えたりするときは、main への PR で行う（complete は origin/main の版と照合する）

## 表記

- Issue と PR はリポジトリ付きで書く（game-harness#44）。「Issue 15」「PR 24」とは書かない
- 段は台帳の番号（W-143 など）で書く
- 件数は数えてから書く。推測の件数を出さない

## エージェントのモデル

- ハーネスが呼ぶすべてのエージェント（分解役・構造化役・実装役など）の設定に、`model_flag` と正確なモデルの ID を書く。CLI の既定のモデルには頼らない
- 実行のたびに、CLI が報告したモデルを記録し、設定と照合する。報告が無い・違う実行は止める（`harness/model_pin.py`。`python -m harness.model_pin` で設定を一斉に検査する）
