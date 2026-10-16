# 仕様変更 Issue のプロトコル（設計）

- 状態: **設計のみ。コードは変更していない。** 実装は Step 7（実機結合）で、最初の仕様変更が必要になったときに行う
- 前提: harness `f4aec60`（Step 3 の二相判定がマージ済み）
- 動機: 外部査読レビューの「P2P 地獄（運用デッドロック）」の指摘

## 1. 問題

Step 3 の P2P は「base で Passed だった全テストが、実装後も Passed」を要求する（`harness/oracle.py` の `p2p`）。
これは機能の**追加**にしか使えない。数値の改定（落下速度・スコア式など）や仕様の変更では、既存テストが**正当に**失敗するようになる。
P2P はそれを先祖返りとして拒否し続けるので、最初の仕様変更で開発が止まる。

quarantine（`oracle.quarantine`）はこの逃げ道にしない。quarantine は**不安定なテスト**を外すためのもので、承認番号（`approved_in`）の意味が違う。仕様変更に流用すると、「P2P 破壊率」の分母から何が外れたのかが記録から分からなくなる。

## 2. 今回の調査で見つけた穴（通常の Issue でも起きる）

base は、Issue ブランチの HEAD で測る。つまり、**分解役のコミットが入った後**の状態で測る（`pipeline.establish_base` → `sandbox_reset` がリポジトリの HEAD に合わせる）。

- 分解役が既存のテストを**書き換え**た → 書き換え後のテストが base の基準になる
- 分解役が既存のテストを**削除**した → そのテストは P_base に入らない

どちらでも、元のテストが守っていた挙動は P2P の外に出る。今の scheduler は、分解役の出力として `test_dir` 配下の変更を一律に許している（`scheduler.process` の `require_only(changed, [unit, in_tests])`）。
分解役は LLM なので、「テストを直したほうが通りやすい」と判断して既存テストを変えうる。これは実装役に対するテスト改ざんの禁止（ホワイトリスト）と同じ型の穴で、出題者の側に開いている。

## 3. 規則

### 3.1 通常の Issue（機能の追加）

1. 分解役に許すのは、`test_dir` への**テストファイルの追加（A）だけ**
2. 既存のテストファイルの**変更（M）・削除（D）・改名（R）は REJECT**（分解役の出力不良。rc=1）
3. 判定: 分解役のコミットについて `git diff --name-status <base_branch>...HEAD -- <test_dir>` を取り、`A` 以外があれば REJECT

### 3.2 仕様変更 Issue

1. **入口**: dispatch が Issue に `ms4:spec-change` ラベルを付ける。付けるのは人間だけ（scheduler も分解役も付けない）。ラベルの無い Issue で分解役が仕様変更を提案しても、3.1 の規則で REJECT される
2. **宣言**: 分解役は、単位定義に次を出す
   ```json
   "supersedes": [
     {"test": "StandaloneCore.Tests.ScoreTests.Clear_Gives100", "action": "modify", "reason": "クリア報酬を 100 → 150 に改定（Issue 本文）"},
     {"test": "StandaloneCore.Tests.ScoreTests.OldBonus_Applies", "action": "remove", "reason": "旧ボーナスの廃止"}
   ]
   ```
   - `test` はテスト名（結果ファイルの名前と照合できる形）、`action` は `modify` / `remove`、`reason` は必須
   - `supersedes` が空の仕様変更 Issue は REJECT（仕様変更である根拠が無い）
3. **差分の検査**: 3.1 と同じ `git diff --name-status` で、M/D の対象になったテストファイルに含まれるテストが、すべて `supersedes` で宣言されていること。宣言外のテストが変わっていれば REJECT
   - ファイル単位の差分からテスト単位の変更を知るには、変更前後のファイルを既存の抽出器（`adapters/csharp_tests.py`。`fast` アダプタの `summarize_files`）にかけ、テストごとの Assert を比べる
4. **判定への反映**
   - `modify` のテストは、自動で `acceptance.required_tests` に加える（F2P。新しい仕様のテストは base で Failed、実装後に Passed でなければならない）
   - `remove` のテストは base の時点で存在しないので、P_base に入らない（特別な処理は要らない）
   - 宣言されていないテストは、通常どおり P2P で守る
5. **承認（H1）**: 承認依頼コメントに「既存テストの変更・削除」節を足す
   - `supersedes` の各行と、変更前 → 変更後の Assert の一覧（抽出器の出力をそのまま並べる。LLM に要約させない）
   - PR にも `ms4:spec-change` ラベルを付ける。人間はこの差分を見て承認・却下する
   - 承認は今と同じく、実装が門を通った後の head SHA に対して 1 回
6. **記録**: runs.jsonl に `kind: "spec_change"`、`superseded_modify`（件数）、`superseded_remove`（件数）を残す

### 3.3 実装の順序（Step 7 で）

1. 3.1 の検査（通常の Issue での M/D/R の禁止）を先に入れる。穴を塞ぐだけで、仕様変更の経路が無くても既存の運用は止まらない
2. 最初の仕様変更 Issue が必要になった時点で、3.2 を入れる

## 4. 研究上の数え方

- 「P2P 破壊率 0%」は、**承認済みの仕様変更で意図して変えたテストを除いた**集合について主張する
- 除いたテストは `supersedes` と PR の承認（SHA つき）で追跡できる。quarantine で外したテストとは別に数える
- 比較対象（門番の無い再試行ループ）では、テストの変更・削除を**すべて**破壊として数える（宣言と承認の仕組みが無いため）。この非対称は論文に明記する
