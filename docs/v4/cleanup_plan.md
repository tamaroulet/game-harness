# 整理の計画（v4 の第 3 層）

- 状態：2026-11-13 に起こし、同じ日に順 1〜6 を済ませた。残りは順 7（手元の片付け）と、agy を箱で呼ぶ形（§4）。
- 方針は[設計と仮説](design.md)の §10。外に出る操作（PR の作成とクローズ・ブランチやタグの削除）と戻せない操作（消す・権限を変える）は、行ごとに操縦士の確認を取る。

## 0. 順番と結果

| 順 | 中身 | 結果 |
|:--|:--|:--|
| 1 | v4 の 3 層・調べものの記録・前提の台帳・v3 の凍結の注記 | game-harness#378（2026-11-13 にマージ） |
| 2 | 開いていた古い PR のクローズ | game-harness#377・#318・#285 をマージせずに閉じた（2026-11-13） |
| 3 | 残す部品を古い部品から切り離す | 済み（整理の PR。ブランチ cleanup/v4-baseline） |
| 4 | v1〜v3 の資産を main から一括で消す | 済み（同じ PR）。タグ archive/pre-v4 で辿れる |
| 5 | 検査を CI に入れる | 済み（同じ PR）：前提の台帳の --strict と、モデルの固定 |
| 6 | 設定と表の規約 | 済み（同じ PR）：C# を拒む設定を外し、CLAUDE.md とルートの README を v4 に書き直した |
| 7 | 手元の片付け | まだ（§5。消す前に行ごとに確認を取る） |

## 1. 保全：タグ archive/pre-v4

- 整理の直前の main（4c671a8、game-harness#378 のマージ）にタグを打ち、GitHub に送った（2026-11-13）。v1〜v3 の文書・コード・試験・ランの公開の記録・決まりの棚卸し（rules_inventory）は、このタグで辿る。
- main には戻さない。docs/archive のような置き場も作らない（操縦士、2026-11-13）。

## 2. 消したものと残したもの

- Git が追うファイルを 826 から 81 にした。
- 消した：H ラインと関門・鎖のベンチ・提案役と外側の周期・総監督・古いゲームの線・A/B の実験の部品と試験（harness/ の 144・tests/ の 256）、experiments/・projects/・rollgame/・tower/・testbed/・launcher/・bench/、docs/ の rebuild・director・tree・bench・testbed・checks・prompts・progress*.yaml・design/ の 40・ops/ の 3、config/ の 17。
- 残した：調べもの（research・research_verify・premises）、モデルの固定（model_pin）、実行環境のアダプタ（box・box_tree）、エージェントの実行口（agy_stream・agy_pinned・agent_cli）、土台（proc・job_object・exitcode・machine）と、その試験（全件 173）。
- 移した：調べ役の頼みの前置きを config/research_instruction.md に。承認ゲートの正本は .github/（ひな形を消した）。main のルールセットの写しは .github/ruleset-main.json に。残す部品の試験（agy の stream-json の読み・利用枠切れの検出・時間切れの出力）は、消す試験から移した。
- 進捗の台帳（docs/progress.yaml と progress.py）と、その決まり（CLAUDE.md の progress review・complete・検証コマンドの PR）も消した（操縦士の決め、2026-11-13）。

## 3. 検査

- 前提の台帳：`python -m harness.premises check --strict` を CI に足した。確かめ直す期限（recheck_by）を過ぎた前提があると CI が落ちる。次の期限は P-007 の 2026-11-18。
- モデルの固定：`python -m harness.model_pin` を CI に足した。今ある設定（config/research.json の調べ役 3 つ）で合格する（終了コード 1 の原因だった古い題材の設定は消えた）。

## 4. agy の全体設定の混入（残り）

- 実物：Windows の上で agy を呼ぶと、リポジトリの外の ~/.gemini/GEMINI.md（副操縦士の決まり）を読む（P-007）。今は調べ役の agy（反証の段）がこれに当たる。
- 手順：調べ役の agy の呼び出しを、箱の中で、全体設定の無い HOME で走らせる形に寄せる（~/.gemini には触らない）。0-b で作るエージェントの実行口と同じ形にする。
- 外の評価（argon_eval）と main の写し（main_mirror）は、総監督の部品と一緒に消した。

## 5. 手元の片付け（順 7、残り）

| もの | 数（2026-11-13） | 扱い |
|:--|:--|:--|
| Git の作業ツリー | 6（main・wt-e5m・wt-e5p・一時の置き場の wt261・wt_old・wt144c） | main だけを残し、ほかは外す（採点のやり直しの道具も消したので、wt-e5m も要らない） |
| 箱のクローン（box-*） | 9 | 公開の記録はタグにある。実装役の生ログは非公開の課題の中身を含むので、手元の非公開の置き場に圧縮して残してから消す |
| 手元の .local の置き場の直下 | 18 のフォルダと 6 のファイル | machine.json・seat・research・oracles（非公開の課題）・bench（資格情報。中を見ない）は残す。ほかは使っている道具を確かめてから、寄せるか消す |
| ACL の写し | 94,264 項目 | Windows の別アカウントで閉じ込める形（P-005、退けた）をやめたので、戻すか消すかを決める |
| WSL の Docker の像 | 箱（gh-box:1・gh-box-proxy:1）と土台の 2 つ | 残す。箱の像からは鎖の課題の部品を外したので、0-b で造り直す（それまで python -m harness.box check は指紋の違いで止まる） |
| 作業ツリーに残った、Git が無視するフォルダ | reports/（週の報告 2 ファイル）と、消した置き場に残った Python の書き出し・空のフォルダ | 消した（2026-11-14、操縦士の ok） |
