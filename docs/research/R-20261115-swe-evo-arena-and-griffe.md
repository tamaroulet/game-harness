# 中流の積み上げの土俵（SWE-EVO）と、公開の口の差分の検査（griffe）

- 記録：R-20261115-swe-evo-arena-and-griffe
- 段：compare
- 決めたい判断：中流のタスクの積み上げを確かめる土俵に SWE-EVO（リリースの間の進化）を使えるか。公開の口の差分を、独自の検査器でなく既存の道具（griffe）で取れるか
- 調べた日時：2026-11-15T01:39:03+09:00
- 調べ役：seat（）
- 照合：verified 10・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | SWE-EVO の課題の始まりは、リポジトリのリリースのタグに一致するコミット（リリースの時点の状態）に限って選んだ。 | リリースの間の進化を土俵にする | academia | primary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C2 | 課題の文は、そのリリースと次のタグのリリースのあいだのリリースノートの差。 | リリースの間の進化を土俵にする | academia | primary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C3 | 既定の設定では、課題の文に、リリースノートと、そこから明示的にリンクされた PR と issue の本文を含める。 | リリースの間の進化を土俵にする | academia | primary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C4 | どのモデルも解けなかった課題はリンクの PR が平均 14.84、最も易しい群は 1.67。PR の数は、リリースの単位の難しさの代わりの指標になる。 | 課題の大きさと難しさ | academia | primary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C5 | どの課題にも、正解のパッチの前で落ちて後で通るテスト（FAIL_TO_PASS）が少なくとも 1 つあり、ほかに退行を見る PASS_TO_PASS が課題あたり平均 793 ある。 | 課題の大きさと難しさ | academia | primary | fact | 2026-05-22 | verified | [SWE-EVO: Benchmarking Coding Agents in Long-Horizon Software Evolution Scenarios（v6）](https://arxiv.org/html/2512.18470v6) |
| C6 | griffe check は、今のコードと Git の参照（タグ・コミット・ブランチ）のあいだで、公開の API の破壊的な変更を調べる。参照の時点のコードは、一時の worktree に作って比べる。 | 公開の口の差分を既存の道具で取る | standards_tools | primary | fact | 不明 | verified | [Griffe: Checking for API breakages（利用者の手引き）](https://mkdocstrings.github.io/griffe/guide/users/checking/) |
| C7 | 検出する破壊的な変更は、引数の移動・削除・種類の変更・既定値の変更・必須への変更・必須の引数の追加と、公開のオブジェクトの削除・種類の変更・基底クラスの削除など。 | 公開の口の差分を既存の道具で取る | standards_tools | primary | fact | 不明 | verified | [Griffe: Checking for API breakages（利用者の手引き）](https://mkdocstrings.github.io/griffe/guide/users/checking/) |
| C8 | 戻り値の型と属性の型の非互換は、静的に判じるのが難しいので、まだ検出しない。 | 公開の口の差分を既存の道具で取る | standards_tools | primary | fact | 不明 | verified | [Griffe: Checking for API breakages（利用者の手引き）](https://mkdocstrings.github.io/griffe/guide/users/checking/) |
| C9 | 何を公開とするかの慣習が griffe の理解と違うと、誤検出が出るか、破壊的な変更を見落とす。 | 公開の口の差分を既存の道具で取る | standards_tools | primary | fact | 不明 | verified | [Griffe: Checking for API breakages（利用者の手引き）](https://mkdocstrings.github.io/griffe/guide/users/checking/) |
| C10 | 属性の値の変化の検出は、ほとんどいつも誤検出を出すので外した。 | 公開の口の差分を既存の道具で取る | standards_tools | primary | fact | 不明 | verified | [Griffe: Checking for API breakages（利用者の手引き）](https://mkdocstrings.github.io/griffe/guide/users/checking/) |

## 抜粋

- C1：「selecting only those samples whose base commit corresponds exactly to a version tag of the repository」
- C2：「we define the problem statement as the release-note delta between that version and its next tagged version」
- C3：「the text of any PRs or issues explicitly linked from that release note」
- C4：「unresolved-by-all instances average 14.84 PRs, compared with 1.67 for the easiest group」
- C5：「at least one FAIL_TO_PASS test validates each gold patch (81% have two or more), with an additional mean of 793 PASS_TO_PASS regression tests per instance」
- C6：「You can specify a Git tag, commit (hash), or even a branch」
- C7：「Parameter moved Parameter removed Parameter changed kind Parameter changed default Parameter changed required Parameter added required」
- C8：「Telling if a type construct is compatible with another one is not trivial, especially statically」
- C9：「you might get false-positives, or breaking changes could go undetected」
- C10：「This check was removed because it almost always emits false-positives」

## 確かめられなかったこと

- 会話の席が 2026-11-15 に原文を読み、抜粋を機械で照合した（SWE-EVO の論文の v6 と、griffe の利用者の手引き）。agy は使っていない（P-007）
- SWE-EVO のデータの欄（PRs の欄に、PR ごとのテストを除くパッチと、テストのパッチがあること）は、論文の本文ではなく、データそのもの（GitHub の SWE-EVO/SWE-EVO の hf_out、コミット 9b83d5a）で確かめた。実験と計画の段 0 の実測に書く
- SWE-EVO の評価のコード（SWE-bench/swebench/harness/test_spec/python.py）は、評価のときに base_commit へ戻さず、イメージの /testbed の状態にパッチを当てる。始まりの状態がタグのコミットと一致するかは、段 0 でイメージごとに確かめる
- griffe が、動的に作る公開の記号や、C 拡張のモジュールをどこまで読むかは、手引きに書かれていない（段 0 で 7 つのパッケージに当てて確かめる）

## 検索語

- （会話の席が原文を直接読んだ。検索は使っていない）https://arxiv.org/html/2512.18470v6
- （同上）https://mkdocstrings.github.io/griffe/guide/users/checking/

## 問い

# 中流の積み上げの土俵と、公開の口の差分の検査

## 段
compare

## 決めたい判断
中流のタスクの積み上げを確かめる土俵に SWE-EVO（リリースの間の進化）を使えるか。公開の口の差分を、独自の検査器でなく既存の道具（griffe）で取れるか

## 判断を変えうるもの
SWE-EVO の始まりの状態が正常なリリースでない記述。リンクの PR の本文が課題の文に入らない記述。griffe が Git の参照の間で比べられない記述、または検出の範囲が狭すぎる記述

## 候補
- 土俵：SWE-EVO のリンクの PR の並び／FeatureBench の同じ版の複数の機能
- 公開の口の差分：griffe／独自の検査器

## 問い
SWE-EVO の課題は、どの状態から始まり、課題の文に何を含み、課題の大きさはどう分かれるか。griffe check は何を比べ、何を検出し、何を検出しないか。
