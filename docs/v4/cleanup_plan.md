# 整理の計画（v4 の第 3 層）

- 状態：2026-11-13 に起こした計画。数は同じ日に main（5b34810）と手元で数えた。手順を済ませたら、その行に結果を書く。
- 方針は[設計と仮説](design.md)の §10。外に出る操作（PR の作成とクローズ・ブランチの削除）と戻せない操作（消す・権限を変える）は、行ごとに操縦士の確認を取る。

## 0. 順番

| 順 | 単位（PR） | 中身 | 確認 |
|:--|:--|:--|:--|
| 1 | この PR（docs/v4-foundation） | v4 の 3 層・調べものの記録・前提の台帳・v3 の凍結の注記・CLAUDE.md の今の運用への合わせ | PR の作成とマージ |
| 2 | PR のクローズ | game-harness#377・#318・#285 をマージせずに閉じる（3 の表） | クローズ |
| 3 | 共通の小道具の移し替え | v4 で残す部品が依る H ライン・台帳の関数を、土台のモジュールに移す（2 の 2-c） | PR |
| 4 | 古い経路の削除（部品ごとに 1 PR） | 鎖のベンチ → 提案役と外側の周期 → H ラインと関門 → 古いゲームの線 → 実験の残り。テストも一緒に消し、毎回すべてのテストを通す | PR |
| 5 | 検査を CI に入れる | 前提の台帳の --strict と、モデルの固定の検査（2 の 5） | PR |
| 6 | 設定と外の道具 | .claude/settings.json の C# の拒み（6）、agy を箱で呼ぶ（7） | PR・設定の変更 |
| 7 | 手元の片付け | 作業ツリー・クローン・.local（8） | 消す |

## 1. 席の状態の文書と記憶に残った古い前提

- 実物：席の現在地の文書の §1 と、記憶の目標と計画に、「B は stateless な外側制御」「比べは Python と MuJoCo の鎖の課題」「正本は v3、新しい版は作らない」が本文のまま残っていた。冒頭に注記を足しただけだった。
- 手順：両方を v4 の体系で本文ごと書き直す（済み、2026-11-13）。記憶の境界のうち、古い目印（W-171）の記載は、フックが今の設定に無いことを確かめたうえで消す（操縦士の確認の後）。

## 2. 死蔵のコードと逆向きの依存

- 実物（`python -m harness.arch_inventory --reach`）：部品の一覧の計 150 モジュール・35,211 行のうち、「残す」の部品は 10,707 行（30%）、「この後捨てる」と「仮説」の部品は 24,504 行（70%）。10-28 の評価では 29,454 行のうち 75%（22,118 行）を「この後捨てる」としたが、消せたのは declare.py だけで、E1〜E5 で増えた。「残す」のモジュールが「残す」でないものに依る所が 52。
- v4 では「残す」の部品の中身も変わる。鎖のベンチ（bench_chain・bench_run・bench_solo・chain_score など）は「残す」に入っているが、v4 の比べでは使わない。
- 一覧の誤り：古いゲームの線の `prop*.py` の型が、提案役（proposer_launch・proposer_stage）まで拾っている。消す前に一覧を直す。

### 2-a. v4 で残すもの

| 部品 | モジュール |
|:--|:--|
| 箱 | box・box_tree・box_run（中身を v4 の比べに合わせて見直す） |
| agy の呼び方 | agy_pinned・agy_stream（今は H ラインの部品に数えられている。土台に移す） |
| 調べものと前提 | research・research_verify・premises |
| 土台 | proc・fileops・job_object・exitcode・infra_retry・transient・textnorm・machine・envcheck・envscan・arch_inventory・tree・toolchain・model_pin |
| 外の評価（仮説） | argon_eval・main_mirror（7 の直しの後も使うなら） |
| PR の要約 | pilot_brief |

### 2-b. 消すもの（部品ごとに 1 PR、この順）

1. 鎖のベンチ：bench_* と chain_*、cycle*・planner・plan_check・plan_loop・reqcov。課題の置き場（非公開の oracles）は消さずに退避する。
2. 提案役と外側の周期：proposer_launch（378 行）・proposer_stage（208 行）・surface（351 行）・surface_apply と、外側の周期の部品（hline_outer・outer_cycles・cycle_report・weekly_report・failure_classes・northstar）。提案役は P-018 に反して作った外側の周期の入口で、索引をすべて Read で開かせる（Grep・Glob を数えない）作りだった。
3. H ラインと関門：hline*（agy_pinned・agy_stream は 3 で先に移す）・gate_*・impacted・fastsuite・size_limits・base_whitelist・implementer_context・narrow_dir・symbolmap・tool_policy。
4. 古いゲームの線：scheduler・pipeline・contract・contractgen・propaudit・propgen・nlgen・testgen・invgen・invrun・oracle・unit_schema・witness・witness_selftest・gdd_check・playtest・canary・diagproj・telemetry・project・adapters/。decompose と spec は、企画書から台帳を作る入口の参考になるので、消す前にタグを付けて残し、v4 で要るなら作り直す。
5. 実験の残り：ab/・provenance（provenance の GEMINI.md の記録は 7 で置き換える）。総監督の部品（director_launch・hline_room・hline_checks・probe・goal_distance・hline_probe）も消す。

### 2-c. 消す前の移し替え（順 3）

v4 で残す部品が外に依るのは次だけ（2026-11-13 に import と子プロセスの呼び方で数えた）：

| 残す部品 | 依っている先 |
|:--|:--|
| research | hline_base・hline_quota |
| argon_eval | hline_base・progress |
| main_mirror | hline_base・hline_git・hline_probe |
| pilot_brief | hline_base |
| box_run | hline_base・fastsuite |
| arch_inventory | bench_start・hline_usage・progress・weekly_report |
| envcheck | project |
| tree | progress |

- 使っている関数を土台のモジュールに移し、残す部品をそこに向ける。台帳（docs/progress.yaml と progress.py）は止めているので、依存を外したうえで退ける（9 の CLAUDE.md と一緒に）。
- 消すたびに、すべてのテストを手元と CI で通す。v3 の記録（v3 の §15 と記録のファイル）は残す。

## 3. 開いている PR

| PR | 中身 | 扱い |
|:--|:--|:--|
| game-harness#377 | E5 の腕の直しと v3 の §0・11-13 の欄 | マージせずに閉じる（操縦士の決め）。B07 のランはコミット 106040d で引ける。§0 に stateless を書いた誤りを main に入れない |
| game-harness#318 | 古い題材の入口と witness_selftest を外す（10-28） | 閉じる。古いゲームの線は 2-b の 4 でまとめて消す |
| game-harness#285 | H ラインの止まっている間の report.md の直し（10-23） | 閉じる。H ラインは 2-b の 3 で消す |

## 4. 表の規約・フック・設計の文書の地層

- CLAUDE.md の進捗の節（progress review・complete）は、台帳を止めた今の運用と合わない。この PR で、今の運用（v4 の 3 層が正本、台帳は止めている）に書き換える。
- 毎ターンの目印（W-171）のフック：2026-11-13 の時点で、リポジトリの .claude/settings.local.json には許可だけでフックは無い。記憶の境界の記載だけが古い（1 で消す）。
- docs/design/ の 42 ファイル：v1〜v3 の地層。コードと試験が 73 のファイルからこの下のパスを引くので、今は動かさない。2-b の削除で引く側が消えた後に、v4 で使うもの（research_protocol.md・premises.yaml）だけを残し、ほかは docs/archive/ に移す。

## 5. 検査の道具の素通り

- 実物：`python -m harness.model_pin` は終了コード 1（projects/unity-2d/pipeline.json の実装役の出力の形が stream-json でない、1 件）のまま、10-23 から直されていない。`python -m harness.premises check` は P-001〜P-006 の期限切れを表示しても終了コード 0（合格）。--strict なら 1。どちらも CI で走っていない。
- 手順：
  1. この PR で P-001〜P-006・P-008 を退け、P-014〜P-020 を照らし直したので、--strict が通る状態にする（期限は P-007 が 11-18）。
  2. projects/unity-2d は古いゲームの線（2-b の 4）と一緒に退ける。それまでに model_pin を通すなら、その設定に stream-json を足す。
  3. CI に `python -m harness.premises check --strict` と `python -m harness.model_pin` を足す。期限切れで CI が落ちるので、前提の期限が来たら照らし直すか退けるかを決める流れになる。

## 6. C# と Unity を拒む設定

- 実物：リポジトリの .claude/settings.json が、C# のファイルの読みと書き（`Read(**/*.cs)`・`Edit(**/*.cs)`）、C# のファイル名を含むコマンド、`dotnet test`、古い C# の題材（falling-blocks・unity-2d）のテストと Core の下を拒んでいる。旧い C# のベンチで、席が隠しのテストを読まないための仕掛け。2026-11-13 にも、説明の文に C# の拡張子を含んだだけのコマンドが拒まれた。
- 手順：Unity の接続の下見の始めに、リポジトリ全体の C# の拒みを外し、拒みは held-out と隠しの課題の置き場のパスだけに絞る。古い C# の題材（C:\src\falling-blocks・C:\src\unity-2d）は、古いゲームの線と一緒に退避する。設定の変更は、操縦士が PR で確かめる。

## 7. agy の全体設定の混入と、外の評価の脆さ

- 実物：Windows の上で agy を呼ぶと、リポジトリの外の ~/.gemini/GEMINI.md（副操縦士の決まり）を読む（P-007）。Flash の補助の呼び出しは、許可を聞けずに空の答えで終わることがある。外の評価（argon_eval）は main の写しの SHA を読むだけで、origin/main と同じかを確かめないので、写しの同期を忘れると全部を消す差分を送って約 25 万トークンを使う。
- 手順：
  1. agy の呼び出し（外の評価・調べものの反証・補助）を、箱の中で、全体設定の無い HOME で走らせる形に寄せる（~/.gemini には触らない）。
  2. argon_eval に、写しの SHA と origin/main の SHA を比べ、違えば止める（または同期してから走る）確かめを足す。
  3. provenance の GEMINI.md の記録は、箱で呼ぶ形になったら外す。

## 8. 作業ツリー・クローン・手元の置き場

| もの | 数（2026-11-13） | 扱い |
|:--|:--|:--|
| Git の作業ツリー | 6（main・wt-e5m・wt-e5p・一時の置き場の wt261・wt_old・wt144c） | PR を閉じた後に wt-e5p・wt_old・wt144c・wt261 を外す。wt-e5m は採点のやり直しの道具が使うので、2-b の 1 の後に外す。main だけを残す |
| 箱のクローン C:\src\box-* | 9（box-dry・box-e4-dev1・box-e4-dev2・box-e5-a-dev1・box-e5-chat-b07・box-e5-chat2-b07・box-e5-lean-b07・box-e5-try1・box-run） | 公開の記録（件数と費用）を写した後に消す。実装役の生ログは非公開の課題の中身を含むので、手元の非公開の置き場に圧縮して残す |
| C:\src\.local | 18 のフォルダと 6 のファイル | machine.json・seat・research・oracles（非公開の課題）・bench（資格情報。中を見ない）は残す。ほかは使っている道具を確かめてから、archive に寄せるか消す |
| ACL の写し | 94,264 項目（席の作業の置き場の acl_backup_20261105-192507） | Windows の別アカウントで閉じ込める形（P-005、退けた）をやめるかを決めた後に、戻すか消す |
| WSL の Docker のイメージ | 箱（gh-box:1・gh-box-proxy:1）と土台の 2 つ | 残す。公式のベンチのイメージは、使う課題の分だけ取る |
