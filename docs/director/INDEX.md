# 総監督の索引（INDEX）

起動したら上から順に読む。迷ったらここに戻る。事実の主張には `path:行` を付け、出典の無いものは「未確認」と書く（decisions.md D-004）。

## 起動時に読む順

1. `C:\src\.local\inbox\report.md` … 現実（進捗・ライン・キュー。H0 後は probe の実測も）
2. `C:\src\.local\inbox\notes\handoff.md` … 前回のセッションからの引き継ぎ（40 行以内）
3. この索引

## 問い → ファイル

| 知りたいこと | 読むもの |
|---|---|
| 何のための工廠か・何を終わりとするか | `docs/director/purpose.md` |
| 機械で守られている約束と、その強制場所 | `docs/director/invariants.md` |
| 何が決まったか・誰が決めるか（権限表） | `docs/director/decisions.md` |
| 過去の失敗と、起草の前に守る規律 | `docs/director/lessons.md` |
| H ラインの構造・未収束の扱い・段の順序 | `docs/design/foundation_v3_review.md`（改訂 6） |
| 進捗のツリー・タスクの検証コマンド | `docs/progress.yaml`（変えるのは `harness.progress` だけ） |
| H ラインの設定（許可マイルストーン・Gate・試行回数） | `config/hline.json` |
| 実装役が変えてはならないパス | `config/protected_paths.json` |
| 収束しなかった What | `C:\src\.local\inbox\TODO.md` |
| 起草中・保留の What | `C:\src\.local\inbox\drafts\what\` |
| 総監督のまだ昇格していない教訓 | `C:\src\.local\inbox\notes\lessons-pending.md` |

（`docs/` と `config/` は `C:\src\game-harness\` の下。総監督は読めるが書けない。変えるのは操縦士の PR）

## 読めないもの（設定で拒否）

- 生ログ `C:\src\.local\out\**`・作業ツリー `C:\src\.local\wt\**`（`inbox/.claude/settings.json:27-28`）
- シェル全般（同 `:4-7`）。Git・CI の状態は report.md の probe 欄で見る

## セッションの終わりにすること

- `notes/handoff.md` を書き直す（決めたこと・未決・次の一手・根拠のパス）
- 新しい教訓は `notes/lessons-pending.md` に 1 行足す（昇格は操縦士の PR）
