# 整理の計画（v4 の第 3 層）

- 状態：2026-11-13 に起こし、同じ日に順 1〜6 を済ませた。2026-11-14 に、E-1 の取り下げに合わせて、リポジトリの不整合と残骸の整理（順 8・9）を足し、手元の片付け（順 7）の対象を、消さずに下調べした（§5）。
- 方針は[設計と仮説](design.md)の §10。外に出る操作（PR の作成とクローズ・ブランチやタグの削除）と戻せない操作（消す・権限を変える）は、下調べの結果の検めの後、行ごとに操縦士の確認を取ってから行う。

## 0. 順番と結果

| 順 | 中身 | 結果 |
|:--|:--|:--|
| 1 | v4 の 3 層・調べものの記録・前提の台帳・v3 の凍結の注記 | game-harness#378（2026-11-13 にマージ） |
| 2 | 開いていた古い PR のクローズ | game-harness#377・#318・#285 をマージせずに閉じた（2026-11-13） |
| 3 | 残す部品を古い部品から切り離す | 済み（整理の PR。ブランチ cleanup/v4-baseline） |
| 4 | v1〜v3 の資産を main から一括で消す | 済み（同じ PR）。タグ archive/pre-v4 で辿れる |
| 5 | 検査を CI に入れる | 済み（同じ PR）：前提の台帳の --strict と、モデルの固定 |
| 6 | 設定と表の規約 | 済み（同じ PR）：C# を拒む設定を外し、CLAUDE.md とルートの README を v4 に書き直した |
| 7 | 手元の片付け | 下調べ済み（2026-11-14。§5）。消すのは、検めと行ごとの確認の後 |
| 8 | リポジトリの不整合：ルートの README の古い方針（白箱を前提にした「共通骨格だけを証明」「受け入れテストを先に見せる」）と「制御核はこれから作ります」 | 直した（2026-11-14。exp/x02-prep の手元のコミット） |
| 9 | リポジトリの残骸：v3 の box の部品と、FeatureBench の制御核の繋ぎ（fb_core 一式） | まだ（§6。main を土台に 1 本の PR。高リスク。手元の box-proxy はマージの後に消す） |
| 10 | agy の全体設定の混入（P-007） | まだ（§4。反証の段の予定を立てるときに） |

## 1. 保全：タグ archive/pre-v4

- 整理の直前の main（4c671a8、game-harness#378 のマージ）にタグを打ち、GitHub に送った（2026-11-13）。v1〜v3 の文書・コード・試験・ランの公開の記録・決まりの棚卸し（rules_inventory）は、このタグで辿る。
- main には戻さない。docs/archive のような置き場も作らない（操縦士、2026-11-13）。

## 2. 消したものと残したもの

- Git が追うファイルを 826 から 81 にした。
- 消した：H ラインと関門・鎖のベンチ・提案役と外側の周期・総監督・古いゲームの線・A/B の実験の部品と試験（harness/ の 144・tests/ の 256）、experiments/・projects/・rollgame/・tower/・testbed/・launcher/・bench/、docs/ の rebuild・director・tree・bench・testbed・checks・prompts・progress*.yaml・design/ の 40・ops/ の 3、config/ の 17。
- 残した：調べもの（research・research_verify・premises）、モデルの固定（model_pin）、実行環境のアダプタ（box・box_tree）、エージェントの実行口（agy_stream・agy_pinned・agent_cli）、土台（proc・job_object・exitcode・machine）と、その試験（全件 173）。このうち box・box_tree は、のちに FeatureBench の経路（fb_agy・fb_core）に替わって使わなくなった（§6 で消す）。
- 移した：調べ役の頼みの前置きを config/research_instruction.md に。承認ゲートの正本は .github/（ひな形を消した）。main のルールセットの写しは .github/ruleset-main.json に。残す部品の試験（agy の stream-json の読み・利用枠切れの検出・時間切れの出力）は、消す試験から移した。
- 進捗の台帳（docs/progress.yaml と progress.py）と、その決まり（CLAUDE.md の progress review・complete・検証コマンドの PR）も消した（操縦士の決め、2026-11-13）。

## 3. 検査

- 前提の台帳：`python -m harness.premises check --strict` を CI に足した。確かめ直す期限（recheck_by）を過ぎた前提があると CI が落ちる。次の期限は P-031 の 2026-11-28。
- モデルの固定：`python -m harness.model_pin` を CI に足した。今ある設定（config/research.json の調べ役 3 つ）で合格する。

## 4. agy の全体設定の混入（順 10）

- 実物：Windows の上で agy を呼ぶと、リポジトリの外の ~/.gemini/GEMINI.md（副操縦士の決まり）を読む（P-007）。今は調べ役の agy（反証の段）がこれに当たる。
- 今の扱い（2026-11-14）：反証の段は、いま予定が無い。予定を立てるまで、Windows の agy で調べ役を回さない（P-007 の注記）。
- 手順（予定を立てるときに）：調べ役の agy を、FeatureBench の腕と同じ形（WSL の箱の中で、全体設定の無い HOME と、agy の宛先だけを通す網）で走らせる部品を作る。~/.gemini には触らない。それまで、Windows の agy の版を照らす部品（agy_pinned と config/agy_digests.json）は残す。
- 外の評価（argon_eval）と main の写し（main_mirror）は、総監督の部品と一緒に消した。

## 5. 手元の片付け（順 7。2026-11-14 に下調べした。まだ何も消していない）

- 消す前の扱い：Git の外の生の記録で、非公開の課題の中身（v3 の鎖の課題）を含みうるものは、中を画面に出さずに圧縮して、手元の非公開の置き場（席の置き場の private の下に、日付のフォルダを作る）に残してから消す。
- C:\src の直下のほかのフォルダ（操縦士のほかの仕事）は、対象にしない。

| もの | 下調べの結果（2026-11-14） | 扱いの案 |
|:--|:--|:--|
| 作業ツリー wt-e5m（枝 feat/e5-metrics） | 未コミットの変更 0。枝に、GitHub に無いコミットが 3 つある（v3 の E5 の採点のやり直しの道具。タグ archive/pre-v4 にも無い） | 作業ツリーを外す。枝は bundle にして非公開の置き場に残してから消す |
| 作業ツリー wt-e5p（枝 feat/e5-arms） | 未コミットの変更 0。枝は GitHub にある | 作業ツリーを外す。枝は残す |
| 一時の置き場の作業ツリー 3 つ（v3 のころの席の置き場） | 中のファイルが、一時の置き場の掃除で消えている（追跡のファイルが全部「消えた」印で、616・684・646。未コミットの仕事は無い）。2 つの枝は GitHub にある。1 つは頭の外れた状態で、そのコミットはどの枝とタグにも無い | `git worktree prune` で外す。頭の外れたコミットは bundle にして残してから外す |
| 箱のクローン 9 つ（v3 の箱の走り。C:\src の直下の box-*） | どれも Git のクローン。大きさと、記録らしいファイルの数は下の別表 | 実装役の生ログを含むので、圧縮して非公開の置き場に残してから消す |
| WSL の Docker の像（v3 の箱 gh-box:1 286MB・中継 gh-box-proxy:1 28MB、土台の python:3.12-slim-bookworm 45MB・debian:bookworm-slim 28MB） | 中継の入れ物 box-proxy（2026-11-07 に作った）は、起き直しの設定が always で、WSL の立ち上がりのたびに起きる（2026-11-14 に 2 回見た）。ほかの像には入れ物が無い。v3 の会話の形の名前つきボリュームは 0 | 入れ物を止めて消し、像 4 つを消す（§6 の部品を消すのと一緒に）。FeatureBench の像 10 個は残す |
| .local の直下（18 フォルダ・6 ファイル） | 今の道具（リポジトリの harness・config と、席の今の置き場）から参照されるものは 0。v3 のころの席のスクリプトが out と inbox を指すだけ。main は古いリポジトリの写し、branches は閉じた PR の写し | 残すもの（machine.json・seat・research・oracles・bench）のほかは、1 つに圧縮して非公開の置き場に残してから消す。screen-oracles は、非公開の課題の中身を含みうるので、中を開かずに oracles の下に移す |
| ACL の写し（94,264 項目。席の置き場） | Windows の別のアカウントで閉じ込める形（P-005）は退けたが、bench のアカウントと拒みの ACL は今も効いている | Unity の閉じ込め方（設計 §9）が決まるまで、今の ACL と写しはそのまま置く |

- .local の直下の内訳（最後に変わった日・今の道具からの参照）：archive（10-18・0）、backup（10-23・0）、branches（11-13・0）、dist（10-03・0）、inbox（10-25・v3 の席のスクリプトから 1）、main（11-13・0）、out（10-10・v3 の席のスクリプトから 6）、runner（10-21・0）、screen（10-15・0）、screen-oracles（10-15・0）、screen-trial（10-15・0）、tmp（09-27・0）、verify（09-13・0）、wt（10-22・0）。直下のファイル 6 つのうち、machine.json のほかの 5 つ（ACL の前の写し 2 つと、v3 のころの小さなスクリプト 3 つ）も、同じ圧縮に入れる。
- 箱のクローンの別表（2026-11-14）：9 つとも、手元のリポジトリ（8 つ）か e5 の作業ツリー（1 つ）を元にした Git のクローンで、v3 の箱の走りのステージごとのコミットを持つ。中を画面に出さず、数だけを数えた。

| クローン | 大きさ | ファイル | 記録らしいファイル（.log・.jsonl・stream） | 未コミットの変更 | 元に無いコミット |
|:--|--:|--:|--:|--:|--:|
| box-dry | 130MB | 8,914 | 29 | 1 | 12 |
| box-e4-dev1 | 455MB | 33,060 | 146 | 2 | 36 |
| box-e4-dev2 | 455MB | 33,062 | 146 | 2 | 36 |
| box-e5-a-dev1 | 249MB | 16,383 | 38 | 2 | 35 |
| box-e5-chat-b07 | 54MB | 3,491 | 7 | 1 | 4 |
| box-e5-chat2-b07 | 96MB | 6,131 | 14 | 2 | 12 |
| box-e5-lean-b07 | 167MB | 11,849 | 44 | 2 | 12 |
| box-e5-try1 | 28MB | 1,745 | 4 | 2 | 2 |
| box-run | 39MB | 2,649 | 10 | 2 | 2 |
| 計 | 約 1.7GB | 117,284 | 438 | 16 | 151 |

  - 元に無いコミットと未コミットの変更は、v3 の鎖の課題の実装役の仕事で、公開の記録（件数と費用）はタグ archive/pre-v4 にある。クローンごと（.git を含めて）圧縮して非公開の置き場に残してから消す。

## 6. リポジトリの残骸の整理（順 9。次の PR）

- FeatureBench の実験は終わった（段 b・段 c・大きい 10 課題の H0 は走らせない。held-out は封を開けずに終える）。harness/fb_core.py を直して残す案はやめ、消す（操縦士の決め、2026-11-17）。
  - 偽物の agy の通しの確認は、harness/fb_agy.py の素の agy で足りる。
  - SWE-EVO で使い回したい部品（agy の実行口・pytest の出力の読み・テストの改変の検出）は、S0 のアダプタを設計 §5-1 の契約で作るときに、FeatureBench に依らない所へ書く。消したコードは、そのとき Git の履歴から読む。
  - 直してから消す二度手間をしないため、この形にする。
1. 依存を断つ：harness/fb_agy.py が読む config/box.json の 2 項目（agy の配布の URL と SHA-512、トークンの置き場）を config/fb_agy.json に移し、読み先を替える。試験を足す。
2. 消す（v3 の box の部品）：
   - harness/box.py・harness/box_tree.py・box/（Dockerfile・中継・WSL の準備のスクリプト）・config/box.json・docs/ops/box.md・tests/test_box.py・tests/test_box_tree.py。
   - config/risk_tiers.json の box の行を外す。
3. 消す（FeatureBench の制御核の繋ぎ）：
   - harness/fb_core.py・config/fb_core.json・harness/core_report.py・tests/test_fb_core.py・tests/test_core_report.py。
   - harness/fb_agy.py の、fb_core の比較条件を登録する所を外し、素の agy だけを登録する。
   - harness/core.py（制御核）と tests/test_core.py は残す（E-3 で使う）。
4. 正本と README：ルートの README の部品の表を直し、§0 の表に結果を書く。
5. 確認：
   - `python -m unittest discover -s tests`・`python -m harness.premises check --strict`・`python -m harness.model_pin`。
   - 偽物の agy で、fb infer から fb eval までを 1 課題通す（利用枠 0。G0-5 の代わり）。
6. PR は 1 本。
   - 高リスク（Tier 2）の区分：tests/・box/・config/box.json・harness/box.py・harness/box_tree.py。
   - 本文の冒頭に、当たった区分とパス・変えた理由・確認したことを書く。土台は main。
7. 手元の後片付け（PR の外。マージの後に、操縦士の確認を取ってから）：
   - WSL の docker の box-proxy のコンテナ（gh-box-proxy:1。再起動の方針は always）を止めて消し、イメージも消す。
   - 消してよい理由：
     - リポジトリで box-proxy に依るのは config/box.json の 2 項目だけで、1 で移す。
     - FeatureBench と SWE-EVO の段 0 の走りは、box-proxy を使っていない。
     - イメージは、タグ archive/pre-v4 の box/ から作り直せる。
