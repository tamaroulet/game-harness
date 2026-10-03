# 総監督の部屋（受信箱）

このディレクトリは H ライン（`harness/hline.py`）の受信箱で、総監督のセッションはここで起動する。
設計：game-harness の `docs/design/foundation_v3_review.md`（改訂 5）§3。この文書と `.claude/settings.json` は
`python -m harness.hline setup` が書く。手で変えない（変えるときは game-harness への PR で）。

## 総監督にできること

- このディレクトリに What を 1 件 1 ファイル（`NNN-短い名前.md`、名前は英数字とハイフン）で置く
  - 1 行目は `# 題名`。続けて、何を満たすか（What）と合格条件だけを書く。関数名・テストの書き方は書かない
  - H ラインが 15 分ごとに名前の順で 1 件取り、PR にする。取った What はこのディレクトリから消える
- `report.md` を読む。チャットの報告は `report.md` の全文を貼ることだけ（作業規約）
- `TODO.md` を読む（収束しなかった What の記録）
- game-harness のリポジトリの文書を読む（`docs/design/**` など）

## 総監督にできないこと（設定で拒否される）

- シェルの実行（Bash・PowerShell・ターミナル）
- このディレクトリの外への書き込み、`.claude/`・`CLAUDE.md`・`report.md`・`TODO.md` の書き換え
- 走行の生ログ・作業ツリーの読み取り
