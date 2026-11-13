# v4：ゲーム開発のための自律開発ハーネスの正本

2026-11-13 に、v3（[direction_and_plan_v3.md](../design/direction_and_plan_v3.md)、凍結）から移った。v4 は役割の違う 3 つの層に分ける。

| 層 | 文書 | 中身 | 変え方 |
|:--|:--|:--|:--|
| 1 | [憲章](charter.md) | 目的・実戦の想定・制約・出口・比べの公平の原則・人が決めること。手段は書かない | 操縦士だけが変える |
| 2 | [設計と仮説](design.md) | 問題の見立て・先人の知見・手元の実測・ハーネスの責務と構成・仮説・反証の条件・Unity への接続・整理の方針 | 前提が変われば節を書き直す。経緯は Git |
| 3 | [実験と計画](experiments.md)・[整理の計画](cleanup_plan.md) | 測った記録・土俵の移行の手順・実験ごとの計画と判定の線と結果・負債の整理の手順 | 実験ごとに書き、済んだら差し替えてよい |

## 根拠の置き場

- 調べものの記録：[docs/research/](../research/)。調べ方の規則は [research_protocol.md](../design/research_protocol.md)。主張ごとに出典と抜粋があり、抜粋は出典の本文で機械に照合する。
- 前提の台帳：[premises.yaml](../design/premises.yaml)。根拠の種類（research・measured・decision・assumption）と、確かめ直す期限を持つ。`python -m harness.premises check --strict` で点検する。
- ランの公開の記録：[bench/records/](../../bench/records/)（件数と費用だけ）。

## 読む順

1. 憲章（数十行）で、目的と変えない条件を知る。
2. 設計と仮説の §4（実戦の想定とハーネスの責務）と §7（仮説）で、何を確かめようとしているかを知る。
3. 実験と計画で、今どの実験の、どの段にいるかを知る。
