# v4：ゲーム開発のための自律開発ハーネスの正本

2026-11-13 に、v3（[direction_and_plan_v3.md](https://github.com/tamaroulet/game-harness/blob/archive/pre-v4/docs/design/direction_and_plan_v3.md)、凍結）から移った。v1〜v3 の文書・コード・ランの記録は、タグ archive/pre-v4 にある（main には残さない）。v4 は役割の違う 3 つの層に分ける。

| 層 | 文書 | 中身 | 変え方 |
|:--|:--|:--|:--|
| 1 | [憲章](charter.md) | 目的・実戦の想定・制約・出口・比べの公平の原則・人が決めること。手段は書かない | 操縦士だけが変える |
| 2 | [設計と仮説](design.md)・[設計の根拠](design_basis.md)・[Unity への接続の方針](design_unity.md) | 設計と仮説：問題の見立て・系の 3 段（上流・中流・外側）の責務と構成・部品と外す条件・採らないもの・仮説・反証の条件・整理の方針・分かっている事実と未解決の問い。設計の根拠：先人の知見・手元の実測・前提の台帳との対応。Unity への接続の方針：実戦の土台の置き場・閉じ込め・アダプタ・検査要綱（節の番号は design.md と同じ） | 前提が変われば節を書き直す。経緯は Git |
| 3 | [実験と計画](experiments.md)・[整理の計画](cleanup_plan.md) | 実験ごとの計画と判定の線と結果・続けて使う方法・負債の整理の手順。閉じた段の全文・測った記録・ログは [archive/](archive/) に中身を変えずに移した（節の番号は元のまま） | 実験ごとに書き、済んだら差し替えてよい |

## 根拠の置き場

- 調べものの記録：[docs/research/](../research/)。調べ方の規則は [research_protocol.md](../design/research_protocol.md)。主張ごとに出典と抜粋があり、抜粋は出典の本文で機械に照合する。
- 前提の台帳：[premises.yaml](../design/premises.yaml)。根拠の種類（research・measured・decision・assumption）と、確かめ直す期限を持つ。`python -m harness.premises check --strict` で点検する。
- v3 までのランの公開の記録（件数と費用だけ。M-02 の B07 の 2 本を含む）：タグ archive/pre-v4 の [bench/records/](https://github.com/tamaroulet/game-harness/tree/archive/pre-v4/bench/records)。

## 読む順

1. 憲章（数十行）で、目的と変えない条件を知る。
2. 設計と仮説の §4（上流・中流・外側の 3 段）と §7（仮説）と §12（分かっている事実と未解決の問い）で、何を確かめようとしているかを知る。
3. 実験と計画で、今どの実験の、どの段にいるかを知る。
