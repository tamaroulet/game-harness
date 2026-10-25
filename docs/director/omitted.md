# 移さなかったものの一覧と理由（W-152）

受信箱（`C:\src\.local\inbox\`）から Git（`docs/director/`）へ移さなかった文書・ファイルの一覧と、その理由。

## 1. 非公開オラクル・参照実装（Git に入れないもの）

- **`drafts/director/W-113/`（隠しテスト `hidden/` と参照実装 `reference/`）**:
  - **理由**: 非公開オラクル（`oracles/`）にあたる資産であり、実装役や AI のプロンプト・作業ツリーから隔離する必要があるため。Git 本線のツリーには入れず、Git 外の管理（オラクル置き場）とする（受入 4、総監督 40 本目 §2 遵守）。

## 2. 過去の対話ログ・済んだ下書き

- **`drafts/director/reply-architect-*.md`（総監督の返事の束、1〜41 本目）**:
  - **理由**: セッションごとの会話往復ログであり、確定した事実や決定事項は `notes/handoff.md` §9、`docs/design/decision_log.md`、および各正本設計書に集約されているため。Git の歴史を肥大化させない。
- **`drafts/director/roadmap.md`**:
  - **理由**: 関門の木と設計メモの有効な内容は `docs/tree/tree.yaml`（W-153）に吸収され、役目を終えたため。
- **`drafts/director/ledger-pr-*.md`（済んだ PR 草稿）**:
  - **理由**: PR #299〜#305 等として既に main にマージ・完了記録されており、下書きとしての役割を終えたため。
- **`drafts/what/` の古い What 群**:
  - **理由**: 過去のタスク消化に伴いすでに完了し、台帳（`docs/progress.yaml` / `docs/progress_archive.yaml`）に結果が記録されているため。
- **`drafts/director/proposal_argon_dag_wiring.md`**:
  - **理由**: Argon による DAG 配線設計・3層レビュー改善の調査提案書であり、正本化する合意・決定に至っていないため、受信箱側の検討用資料として残す（総監督 42 本目 §3.3）。

## 3. 受信箱に残し、下書きとして更新し続ける文書

以下の 3 文書は、総監督が日々返事のたびに直接更新するため、受信箱に残す（総監督 40 本目 §2 合意）。台帳や木に触れる PR のついでに Git の正本へ同期する。

1. **`notes/handoff.md`**（総監督の意志・前提の更新用下書き）
2. **`drafts/director/INDEX.md`**（総監督の原則・索引の下書き）
3. **`drafts/tree/tree.yaml`**（タスク木の下書き）
