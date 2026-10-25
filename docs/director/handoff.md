# 引き継ぎ（総監督）

ここに書くのは、セッションをまたぐ意志（未決・前提の仮定・次の一手の意図）と、会話の中にしか無かった約束ごと・やり方。現在地・タスクの状態は `report.md` と game-harness の `docs/rebuild/README.md`・`docs/tree/tree.yaml` で見る。

（2026-10-25 に、新しいセッションへ移るために書き直した版。14〜36 本目の往復の全文は `drafts/director/reply-architect-20261024-*.md` にある。前の版の 6b（12〜36 本目の 1 行ずつ）は §9 に縮めた。W-152 にて正本化。総監督は受信箱の下書きを更新し、台帳・木の PR で正本へ同期する）

## 0. まず読むもの（この順）

1. 受信箱の `report.md`。H ラインは止まっている（`stopped.json`）。report.md は副操縦士が `python -m harness.hline_stopped report` で書いたもの。冒頭の書いた時刻と origin/main の HEAD を見る
2. この handoff
3. `docs/director/INDEX.md`（原則・権限）
4. 要るときに：game-harness の `docs/rebuild/README.md`、`docs/tree/tree.yaml`（木の正本）、受信箱の `drafts/tree/tree.yaml`（下書き）・`drafts/tree/README.md`（木の形と欄）

## 1. 登場する役と語彙（16 本目で合わせた。古い語は使わない）

| 呼び名 | 誰か | すること |
|---|---|---|
| 操縦士 | 人間 | L5（方針・権限・ブランチ保護・ループの再開・最終目的の文）。頼みを受信箱へ貼る |
| 会話の席・副操縦士 | Claude Code の対話のセッション（ワーカー） | PR を作る、差分を `review\<番号>\` に置く、総監督の合格と CI の緑の後にマージする |
| 総監督 | このセッション | プランナーのうち最終目的と要件の側。木を書き、PR を検める |
| Argon | Gemini 4。状態を持たないレビュアー（W-172） | 木の下書きなどの事前・事後の評価。批評は副操縦士が貼る |
| planner | H ラインの計画役 | プランナーのうちタスクと受入テストの側（まだ木につないでいない） |

- 標準の語彙：最終目的（objective）→ 要件（requirement）→ タスク の DAG。要件の status は 未達・仮説・自己検証のみ・検証済み の 4 値。verify_by は `test:independent`・`reviewer`・`human` の 3 値
- リスクの層：Tier 0／1／2。**Tier 2（高リスク）は単独の PR にし、マージの前に必ず `review\<番号>\` に置いて総監督の合格を待つ**
- 「セクター」「rebuild-0〜6」は #301・#302 で消した。group は `rebuild` 1 つ

## 2. 頼みと返事のやり方（会話の中で決まったこと）

- 頼みは、操縦士が `<pasted_content>` で貼る（副操縦士が書いた知らせ）。1 度だけ、知らせが `drafts/director/reply-architect-20261024-33.md` として置かれた。そのときの返事は `-33-reply.md`
- 返事は `drafts/director/reply-architect-20261024-<n>.md`（36 本目まで。日付が変わっても今は `20261024` の番号を続けている）。返事を書いたら、この handoff の §9 に 1 行足す
- 返事の形：1. 結論（合格・不可・受けた）→ 確かめたこと → 直すこと → 小さなこと（直さなくてよい）→ 次。数と SHA を書く
- マージの任せ（操縦士の決定、31 本目・`docs/design/decision_log.md` の 2026-10-25 の行）：総監督が合格にした PR のマージ（`ms4:approved`・マージ・同期）は副操縦士が行う。マージの前の門は、総監督の検めと CI。W-154（`req.risk-tiers`）がいずれ機械で強制する形に置き換える。#301・#302 のラベルは操縦士その人が付けた

## 3. PR の検め方（同じ失敗を 3 回した。必ず守る）

- 実物は `C:\src\.local\inbox\review\<番号>\patch.diff` と `README.md`。逐語で全部読む。main は読めないので、マージや CI は副操縦士の知らせとして受け、そう書く
- 読む前に確かめること：
  - README の HEAD の SHA・基の main の SHA・バイト数が知らせと合うか。**前の版と同じなら直しが入っていない**（24・25 本目。原因は総監督が書き出しの例を相対パスで書き、リポジトリの中に書かれたこと）
  - 日本語が化けていないか（UTF-8 を Shift_JIS で読んだ形、`讀懆ｨｼ` など。21・33 本目）。化けていたら `git diff --output=C:\src\.local\inbox\review\<番号>\patch.diff origin/main...origin/<枝>` で書き出し直してもらう。**例は必ず絶対パスで書く**
  - README と知らせの食い違い（CI が「実行中」と「PASS」など）
- 中身で見ること（これまで見落としかけたもの）：
  - 試験が偽物で確かめたい道を塞いでいないか（34 本目：`write_report` の試験が各節を偽物にしていた）
  - 「あれば A、無ければ B」の二段構えが、違う中身を黙って読む道になっていないか（33 本目）
  - 受入の文の字面が全部満たされたか（27 本目：slug に `rebuild-N:` が 20 件残っていたのを #301 で見落とした）
  - `progress complete` は origin/main の検証コマンドと照らす。だから検証コマンドを変える PR の中でタスクを完了にできない。完了はマージの後の小さな PR で（23 本目、総監督の下書きの誤り）
- game-harness の clone `C:\src\game-harness` は古い（最後に見たとき 9fdaabe）。読むなら `.git/refs/heads/main` を GitHub の main と比べ、違えば「古い clone で読んだ」と書く
- WebFetch は要約。根拠にしない。使ったら「要約で読んだ」と書く

## 4. 木（目的・要件・タスク）

- 正本は main の `docs/tree/tree.yaml`（#301 で入った）。下書きは受信箱の `drafts/tree/tree.yaml`。流れ：総監督が下書きを直す → 副操縦士が次の PR で正本へ写す → 写した後、下書きと正本は同じになるはず（違えば README に書いてもらう）。写しを機械が PR にする道（W-151）は保留
- 最終目的（操縦士が確定。台帳の `project_goal` と同じ文字列。**1 行で書く**。`>-` で折ると空白が入る）：
  「人の手を借りずに、計測しながら自己改善し続けられる開発ハーネスとゲーム生成パイプラインを作り、3D の物理演算を含むゲームを題材にした長期開発で、単一コンテキスト型のエージェントと統制された比較実験を行い、優劣を判定する（ハーネス側が勝つことは完了の条件にしない）」
- `harness/tree.py` の検査：objective は 1 つで `project_goal` と同じ（無い・空は落とす）、id が一意、欄がそろう、status 4 値・verify_by 3 値、depends_on の先がある・循環が無い、tasks が台帳にあり withdrawn が無い、未完了（pending・in_progress・stopped）のタスクがちょうど 1 つの要件にある。台帳は過去ログと合わせて読む
- 検証済みの要件：`req.ledger-light`（W-176・W-177）、`req.ledger-triaged`、`req.tree-in-git`（W-153・W-157）
- **下書きと正本の違い（今）：** `req.no-human-relay` の status（検証済み）と evidence の 39 本目の 1 項。`req.risk-tiers` の depends_on に `req.progress-reviewed`。`req.progress-reviewed` の depends_on `[req.no-human-relay]` と proposals（影響束）（40 本目）。`req.risk-tiers` の evidence にレビューの設計と自律マージの始める条件（41 本目）
- 1 つのタスクは 1 つの要件にだけ書く（32 本目：下書きの `req.harness-healthy` に W-154 が重なり、検査に落とされた。総監督の誤り）
- 木に入れなかった Argon の案と理由：「回帰ベンチマークを通じて」（H2 を決め打ちにする）、W-171 の取り下げ（操縦士の方向 1）、第 1 弾での ledger-light の「検証済み」（根拠が無かった）

## 5. 台帳と過去ログ（#299〜#305 で決まった形）

- 現役 `docs/progress.yaml`、過去ログ `docs/progress_archive.yaml`（済み 166 件）。現役の最上位に `archive: docs/progress_archive.yaml`
- ref から読むのは `progress.read_ref`（8 か所を寄せた、#300）。作業コピーは `progress.load`。過去ログのパスは、台帳が属するリポジトリの根（`p.resolve().parent.parent`）からの相対で 1 つに決める。フォールバックは無い（#305）
- 宣言があって過去ログが読めない・形が違う → `ProgressError`。宣言の無い古い ref は今までどおり
- 呼び手（#305）：`base_state` は `ValueError` に包む。`on_integration` は例外を出す（`False` を返さない）。`intake` は `skipped` に理由を書き、What を受信箱に残す。`write_report` は `report.md` に「## ⚠️ 台帳（過去ログ）を読めません」と理由・人間作業の行を出す
- `stack` は先頭で `on_integration` を呼び、統合ブランチへの書き込みはその後（副操縦士の説明。総監督は読んでいない）
- `progress.selectable`：検証の書かれていないタスクは、group に依らず選ばない（#301）

## 6. 次の一手の意図

1. **PR の検め方（39 本目から）**：低リスクの PR は `review\` を使わない。副操縦士が `hline_stopped report` で写しを合わせ、知らせるのは番号・SHA・ファイルの一覧だけ。総監督は `branches\<番号>\.branch.json` の SHA を照らし、変わったファイルを main の写しと全文で読み比べる。ファイルの一覧は知らせに頼る（git を走らせられない）。高リスクの PR は `review\<番号>\patch.diff` にも置く。`req.no-human-relay` は下書きで検証済みにした
2. **木の写しは単独の PR にしない**（39 本目）。次に台帳か木に触れる PR に入れてもらう
3. **W-152（40 本目）**：着手してよいと返した。書き続ける文書（handoff・INDEX・木）は、木と同じく「受信箱の下書き → 正本 `docs/director/` へ、ついでの PR で写す」にし、読む順は変えないよう頼んだ。PR が来たら、受入 1 の文がそう直っているかを見る
4. **Argon の案（`drafts/director/proposal_argon_dag_wiring.md`、40 本目）**：受けたのは W-154 の書き直し（セクターの残り）・W-135 と W-172 の依存・構造化の判定の行。変えて受けたのは、影響束を木の proposals に置くことと、`req.progress-reviewed` の依存を `[req.no-human-relay]` だけにすること。受けなかったのは「Scorecard が Low なら自律マージ」。L5 の 2 点は 41 本目の設計書（`drafts/director/proposal_review_architecture.md`）で決まった。W-172 は全部を読む形のまま、まとまりごとの事前の確認は置かない。台帳の変更は、W-152 とは別の 1 本の PR で出してよいと返した
5. **レビューの設計（41 本目）**：人が呼ばれるのは高リスク・HIGH・3 回続けての落ち・大きな節目だけ。直してもらうことは 2 つ。(1) 流れ図で、Tier 2 が AI の所見だけで自律マージに出る道を消す（AI の所見は止める向きにだけ効かせる）。(2) 低リスクの自律マージは、層を触れたパスから決める CI の検査が入るまで始めない。それまでは今の任せ（総監督の検め＋CI）を続ける。decision_log への記録も頼んだ
2. **写しは読める**：`C:\src\.local\main`（根の `.mirror.json`）と `C:\src\.local\branches\<PR>\`（`.branch.json`）。§3 の「main は読めない」は古い。読むときは `.mirror.json` の SHA を書く
3. **W-152**（現在地）：着手の前に、総監督の文書の置き場と書ける場所が変わるか（変わるなら L5）を知らせるよう頼んだ
3. ループの再開は操縦士が決める。再開した後に最初に `report.md` が書き直されたら、「目的までの距離」を前と照らす（分ける前は R4 の前提タスク 78／80）
4. 受信箱の直下に What を置かない（H ラインは止まっている）

## 7. 残っていること

### 小さな直しの宿題（急がない。ついでの PR で）
- `tests/test_tree.py` の `status: done`（台帳の status の語と合っているか）
- `progress.py` の `SECTORS` の残り
- `hline_stopped` の使わない `git` の関数
- `docs/design/decision_log.md` の 2026-10-25 の行の「タスク」欄が「W-154、W-177」。「W-154 が置き換える」の意味なら次に触るときに直す。「3 タスク単位の事前・事後確認」は操縦士の言葉として残す（意味は総監督には分からない）

### まだ届いていないもの
- Argon の批評の全文と、欠陥の棚卸し D1〜D21・E1〜E5 の置き場
- #297 の CI の秒の前後

### 人の手番（L5）
- main のブランチ保護と保護パス（取り下げた W-130・W-131 の中身。`req.protected-checks` の proposals）
- AI 用の GitHub アカウント（承認の検査 `.github/scripts/ms4_approval_gate.py:18` が人と AI を区別できない）
- ループの再開
- W-113 L2 の照合・W-114：材料は `drafts/director/W-113/`。**隠しテストは Git に入れない**

## 8. 前提の仮定

### 今も使えるもの
- A13（実測：`config/hline.json`）：Gate 1 は LLM を使わない。監修役は取り込みの段だけで合格を決めない
- A14（実測：roadmap D-128）：点を決めるもの（仕様・判定役・オラクル・固定の計画・モデルと版・試行の回数・TTL・Gate 1・環境）を変えたら基準を取り直す
- A16（実測：`docs/design/review_after_baseline.md`）：W-89 の基準は 102/110（0.927）。立て直しで Gate 1 と経路が変わるので、取り直してから使う
- A28（実測）：W42 の試行 96 回のうち `error_max_turns` 22 回。大きい What はターン切れで未収束になる
- A47（実測：`progress.py` の `EXECUTORS`）：`by` を省いたタスクは `hline`。執行者は `pilot`・`hline`・`executor`・`director`
- ADR-004 §1 の欠陥 5 つ（H ラインを止めた理由）：①合否が終了コードとラベル 1 枚 ②試験の自己作成（71 件中 63 件） ③承認と門が機械で強制されていない ④走らせ役が Git の外 ⑤環境の直書き

### 手持ちのまま
- A27：AGENTS.md（W-108、止めた）が 1 回目の落ちを減らす
- A18・A19：コンテナでのファイルのロック、VLM の審査の確かさは未確認
- A76：Argon（Gemini 4）の枠は、ベンチの実装役 agy（Gemini）と同じ枠を分け合う

### 崩れたもの（使わない）
- A32・A35〜A37・A40〜A43・A45（R4 の段取り・runner の前提）、A26（W42 の計器）

## 9. 往復の記録（1 本 1 行。詳しくは各返事のファイル）

- 14（10-24）：段 1〜5 の再編成の案。段 1 だけ条件付きで受けた
- 15：反論を受け、台帳の分離を先に回してよいとした（条件 3 つ：report の 3 節が同じ文字列・項目の集合が同じ・トークンの前後）
- 16：語彙の資料（`review\vocabulary_and_architecture_rebuild.md`）を受けた。§1 の役と語彙
- 17〜19：木の下書き 第 1〜3 弾（Argon の事前評価を 2 回受けた）。仕分け：取り下げ W-158・W-159・W-163・W-165・W-166・W-109・W-110、保留 W-151（操縦士が確かめた）
- 20：#299（台帳の分離）。直に読む 8 か所が過去ログを読まないので「検証済み」にしなかった
- 21：#300（`read_ref` に統一）。`req.ledger-light` を検証済みに。過去ログの守りを頼んだ
- 22：W-153 を `drafts/what/W-153.md` に起票
- 23〜26：#301（木の投入・tree_docs の撤去・selectable を全タスクへ）。6 点を直してもらい合格。24・25 は差分が古いままだった
- 27〜28：#302（W-153・W-157 の complete、slug の rebuild-N 20 件と試験）。合格。マージ `5b51bfa`
- 29：`req.tree-in-git`・`req.ledger-triaged` を下書きで検証済みに
- 30〜31：#303（W-177、archive 宣言と `ProgressError`）。合格。マージ `326932d`。マージの任せを受けた
- 32：#304（W-177 の complete・木の写し・任せの記録）。合格
- 33〜35：#305（例外の受け方）。不可・不可・合格（§5 の形）
- 36（10-25）：#305 のマージ `bef2ed6`（CI の test 4 分 48 秒）を受けた。次は W-150
- 37：#306（W-150 の complete、`req.ledger-light` の evidence の写し、HEAD `2ebec20`）。合格。写しを直に読み、setup が古く書き込みの拒否が無いのを見つけた。setup の走らせ直しと W-152 の前の知らせを頼んだ
- 38：#306 のマージ `d0fb02a`、setup の走らせ直しを実物で確かめた。次の PR を枝の写しで検める試しを頼んだ
- 39：#307（木の写し、`dc4f5ca`）を枝の写しで検めて合格。`req.no-human-relay` を下書きで検証済みに。木の写しの単独の PR はやめる
- 40：#307 のマージ `01bb2e9` を受けた。W-152 は着手してよい（書き続ける文書は下書き→正本の流れに）。Argon の案は一部を受け、自律マージは受けず、L5 の 2 点を操縦士へ
- 41：W-152 の着手を受けた。レビューの設計書は向きを受け、流れ図の Tier 2 の抜け道を直すことと、自律マージは層の機械の検査の後から始めることを頼んだ
