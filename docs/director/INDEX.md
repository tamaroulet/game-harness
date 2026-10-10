# 総監督の索引（INDEX）

起動したら上から順に読む。迷ったらここに戻る。事実の主張には `path:行` を付け、出典の無いものは「未確認」と書く（decisions.md D-004）。
総監督の知識の正本はこのディレクトリ（受信箱 `drafts/director/`）だけ。書き手は総監督。写しは持たない（D-024）。

## 起動時に読む順

1. `C:\src\.local\inbox\report.md` … 現実（進捗・ライン・キュー・probe の実測）
2. `C:\src\.local\inbox\notes\handoff.md` … 読む場所と未決だけ（40 行以内。D-025）
3. この索引

## 問い → ファイル

| 知りたいこと | 読むもの |
|---|---|
| 何のための工廠か・何を終わりとするか | `drafts/director/purpose.md` |
| 機械で守られている約束と、その強制場所 | `drafts/director/invariants.md` |
| 何が決まったか・誰が決めるか（権限表） | `drafts/director/decisions.md` |
| 過去の失敗と、起草の前に守る規律 | `drafts/director/lessons.md` |
| H ラインの構造・未収束の扱い・段の順序 | `docs/design/foundation_v3_review.md`（改訂 6） |
| 進捗のツリー・タスクの検証コマンド | `docs/progress.yaml`（変えるのは `harness.progress` だけ） |
| H ラインの設定（許可マイルストーン・Gate・試行回数） | `config/hline.json` |
| 実装役が変えてはならないパス | `config/protected_paths.json` |
| 収束しなかった What | `C:\src\.local\inbox\TODO.md` |
| 起草中・保留の What | `C:\src\.local\inbox\drafts\what\` |

（`drafts/` と `notes/` は受信箱 `C:\src\.local\inbox\` の下。`docs/` と `config/` は `C:\src\game-harness\` の下で、総監督は読めるが書けない。変えるのは操縦士の PR）

## 情報の経路（D-025）

- 事実：report.md の probe の節から読む。人が中継した事実は、出所を開いて確かめるまで使わない
- 操縦士の意図：読み方に迷ったら、推測で書かずにその場で聞く

## 読めないもの（設定で拒否）

- 生ログ `C:\src\.local\out\**`・作業ツリー `C:\src\.local\wt\**`（`inbox/.claude/settings.json:27-28`）
- シェル全般（同 `:4-7`）。Git・CI の状態は report.md の probe の節で見る

## セッションの終わりにすること

- 決定を変えたなら、古い記述を受信箱で 1 回 grep して洗う（D-025）
- `notes/handoff.md` を書き直す。事実・決定は書かず、読む場所と未決だけ
- 新しい教訓は `drafts/director/lessons.md` に直接 1 行足す
