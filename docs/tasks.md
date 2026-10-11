# 段 3・段 4 のタスク別の補足説明（正本）

台帳（`docs/progress.yaml`）の各タスクの、なぜ・何を・どう確かめるか・誰が・どの横木に寄与するか。
台帳の `title`・`purpose`・`deliverable`・`verification`・`by`・`serves` と同じ文言で書く。食い違うと `tests/test_ledger_double_check.py` が CI で落ちる（台帳を変える PR は、この表も同じ PR で直す）。

## 目的の梯子（横木）

| 横木 | 台帳 | 到達点 | オラクル |
|---|---|---|---|
| R3-a | W-21 | 工廠が B06 の本体を仕様から作る | `tests/launcher_judge.py`（総監督の判定役） |
| R3-b | W-22 | 工廠が物理の課題 3 本を作る | 3 つの判定役 |
| R3-c | W-23 | 課題 10 本・非公開オラクル・土台の凍結（天井の検査に合格） | `harness.bench_ceiling --check` |
| R3（C5） | W-24 | 計画役の What による工廠が、チャット型に有意に勝つ | `harness.bench_stats --verdict` |
| R4（C4。最終目的） | W-25 | Outer Loop の週次の周期が 4 週続き、改善が計器を良くし、ベンチの点が下がらない | `harness.outer_cycles --min 4 --improved 1` |

## タスク

| ID | 題 | 目的・背景 | 成果物 | 検証 | 執行者 | 横木 |
|---|---|---|---|---|---|---|
| W-20 | 段 3 の開始の PR（B06 の仕様と判定役・目的の梯子） | 段 3 の物差しの最初の課題と判定役を置いた（完了） | docs/bench/B06/spec.md、tests/launcher_judge.py | `python -m harness.progress check` | 操縦士（手動） | R3-a |
| W-62 | 台帳の正本の資料（タスク別の補足説明）と二重の照合を main に置く | 設計意図を会話から外し、台帳の欄と docs/tasks.md に永続させる。台帳を変える PR は、大局（横木）との整合とオラクルの実在を CI で確かめられる（病理 ①②③、要綱 1） | docs/tasks.md、tests/test_ledger_double_check.py、台帳の purpose・deliverable・serves の欄 | `python -m unittest tests.test_ledger_double_check` | 操縦士（手動） | R3-b |
| W-26 | タスクを宣言しない What を取り込まない（台帳に無い・完了したタスクの What も） | 台帳を進めずにコードだけが積まれる抜け穴を塞ぎ、すべての作業を台帳のタスクとして記録させる（病理 ③ の事前の保証） | H ラインの取り込みの規則、雛形の CLAUDE.md、tests/test_hline_task_required.py | `python -m unittest tests.test_hline_task_required tests.test_hline_queue tests.test_hline_what_verification tests.test_hline_ledger_deps` | 工廠（自律） | R3-a |
| W-27 | 進捗の記録を Gate 1 の最後の段にする（記録の検証の落ちは試行の不合格、台帳の異常は未収束。どちらも積まない） | 台帳が進まない変更を積まず、記録の検証の落ちは What を捨てずに次の試行で局所に直させる（要綱 4） | H ラインの記録の段、tests/test_hline_record_in_gate.py | `python -m unittest tests.test_hline_record_in_gate tests.test_fatal_failfast tests.test_hline_queue tests.test_progress_order_free` | 工廠（自律） | R3-a |
| W-21 | 横木 R3-a：課題 B06 の本体を工廠が仕様から作り、判定役を通す | 工廠が 3D 物理の課題を仕様だけから作れることの最初の実証（C5 への第一歩、物理の成立） | launcher パッケージ | `python -m unittest tests.launcher_judge` | 工廠（自律） | R3-a |
| W-28 | 収束しなかった What を、最後の試行の変更と失敗の出力から再開する | 1 か所の誤りで全部をやり直す非効率を無くし、前回の差分から回復させる（要綱 4） | H ラインの再開の仕組み、tests/test_hline_resume.py | `python -m unittest tests.test_hline_resume tests.test_hline_queue` | 工廠（自律） | R3-b |
| W-29 | 監修役（機械）：What を取り込む前に、台帳のタスクとオラクルの実在に照らして差し戻す | オラクルの無い What と形の崩れた What を、実装の前に止める（要綱 1・3、病理 ③ の事前の保証） | 取り込みの段の機械の監修、tests/test_audit_machine.py | `python -m unittest tests.test_audit_machine tests.test_hline_queue` | 工廠（自律） | R3-b |
| W-30 | 監修役（LLM）：機械の監修を通った What を、台帳のタスクと大局に照らして批判し、差し戻すか通す | 起草役（総監督・計画役）と監修役を分け、タスクの目的と横木に照らして的外れな What を差し戻す（要綱 3、病理 ②） | 取り込みの段の LLM の監修（指示は docs/prompts/auditor.md）、tests/test_audit_llm.py | `python -m unittest tests.test_audit_llm tests.test_hline_queue` | 工廠（自律） | R3-c |
| W-63 | 目的までの距離を report.md の先頭に出す（横木ごとの達成の量と前提タスクの完了率） | 進み具合をタスクの消化数でなく、C5・C4 の達成要件に対する量で常に見せる（病理 ④） | harness/goal_distance.py、report.md の「## 目的までの距離」の節、tests/test_goal_distance.py | `python -m unittest tests.test_goal_distance` | 工廠（自律） | R3-c |
| W-64 | タスクの完了の前後で目的までの距離を記録し、後退に印を付ける | タスクを 1 つ終えるたびに、目的に近づいたか・どこかを壊していないかを記録で示す（病理 ③ の事後の保証） | 目的までの距離の履歴、report.md の「直近の完了と距離の変化」、tests/test_goal_history.py | `python -m unittest tests.test_goal_history` | 工廠（自律） | R3-c |
| W-65 | H ラインは、走っているコードが統合ブランチの先端（無ければ base）と違うとき、What を取り込まず処理もしない | 積んだ自分の変更（再開・監修）を読み込まないまま古いコードで走る穴を塞ぎ、その状態を report.md に出す（10-11、W-28 の再開が一度も働かなかった。病理 ④） | 走行の前のコードの版の照合、report.md の H ラインの節の「コード」の行、tests/test_hline_code_version.py | `python -m unittest tests.test_hline_code_version tests.test_hline_queue` | 工廠（自律） | R3-c |
| W-66 | 未収束の What の再開の材料を report.md に出し、試験が最後まで走って通った数が増えていれば 1 回だけ自動で続ける | 再開の材料の有無を総監督が見られるようにし、前進が確かな What だけを人の置き直しなしに続けさせる。前進の無い試行（構文の誤り・途中の落ち）には続けない（要綱 4） | report.md の未収束の行の再開の材料、自動の延長、試行の記録の通った試験の数、tests/test_hline_resume_extend.py | `python -m unittest tests.test_hline_resume_extend tests.test_hline_resume` | 工廠（自律） | R3-c |
| W-67 | 試行のトークンを、入力・キャッシュの書き込み・キャッシュの読み出し・出力に分けて report.md に出し、直近 20 件の歩留まりと燃費を出す | 1 件あたり約 100 万トークンの中身と歩留まりを測り、燃費の手（W-69・W-73・W-74）の効き目と、本走の前の歩留まりの関門（W-48）の値を数字で出す（病理 ④） | 試行の記録の内訳、report.md のキューの行と計器の内訳・直近 20 件の行、tests/test_hline_usage_breakdown.py | `python -m unittest tests.test_hline_usage_breakdown` | 工廠（自律） | R3-c |
| W-68 | 比較の規約（両条件でそろえるもの・違えるもの・評価項目・無効の走行）を docs/bench/protocol.md に置く | チャット型を不利に縛って勝つ疑いを、凍結の前の事前登録で外す（C5 の科学的な成立） | docs/bench/protocol.md | `python -m harness.reqcov --spec docs/bench/protocol.md --list` | 操縦士（手動） | R3-c |
| W-69 | 実装役に渡す目次を、What が名指すモジュールとその import 先の節に絞る | 呼び出しのたびに渡す目次（上限 30,000 字）を What に要る範囲に絞り、入力のトークンを減らす（燃費。効き目は W-67 の内訳で測る） | 実装役の目次の絞り込み、試行の記録の目次の字数、tests/test_hline_symbol_scope.py | `python -m unittest tests.test_hline_symbol_scope tests.test_symbol_map` | 工廠（自律） | R3-c |
| W-70 | 照合で、受信箱の下書きを base の木に重ねて走らせる（重ねるファイル・繰り返しの回数・期待する合否） | シェルを持たない総監督の下書き（仕様・判定役・参照実装・隠しテスト）を、操縦士の手を使わずに PR の前に機械で確かめ、検証の負荷を操縦士に集めない | 照合の宣言の overlay・repeat・expect、tests/test_hline_checks_overlay.py | `python -m unittest tests.test_hline_checks_overlay tests.test_hline_checks` | 工廠（自律） | R3-c |
| W-71 | ベンチの走行を、非公開オラクル・受信箱・本線の生ログを読めない OS のアカウントで回し、漏れの跡のある走行を無効にする | Gate 1 は実装役の書いた試験を実行するので、道具の設定の禁止では非公開オラクルを守れない。読めないことを OS で保証し、漏れの跡を記録で検める（C5 の成立） | harness/bench_guard.py、docs/ops/bench_account.md、tests/test_bench_guard.py | `python -m unittest tests.test_bench_guard` | 工廠（自律） | R3-c |
| W-72 | 台帳の after（弱い依存）を H ラインが守り、上流が未収束でも後続を止めない。未収束ごとに、凍結した下流と本線への影響を出す | 副次の道具の未収束が本線を道連れに凍結させない仕組みを持ち、凍結の影響を総監督がすぐ選り分けられるようにする（一本鎖の脆さ） | 台帳の after の扱い、report.md の未収束のトリアージの行、tests/test_hline_soft_deps.py | `python -m unittest tests.test_hline_soft_deps tests.test_hline_ledger_deps` | 工廠（自律） | R3-c |
| W-73 | 実装役の空転（同じ呼び出しの繰り返し・編集の無いターンの連続）を、呼び出しの途中で止める | 前進の無いターンにトークンと時間を使わない。呼び出しの後ではなく途中で止める（燃費と実時間） | 実装役の出力の流れの読み取りと途中の停止、tests/test_hline_spin_stop.py | `python -m unittest tests.test_hline_spin_stop tests.test_hline_turn_cap` | 工廠（自律） | R3-c |
| W-74 | 前の差分から続ける試行は、ターン上限を下げ、渡す失敗の出力を落ちた試験の分だけにする | 残りが局所の直しになった試行に、最初の試行と同じ文脈とターンを使わない（燃費。文脈の圧縮） | 続きの試行のターン上限と失敗の出力の絞り込み、tests/test_hline_follow_up.py | `python -m unittest tests.test_hline_follow_up tests.test_hline_turn_cap` | 工廠（自律） | R3-c |
| W-75 | Gate 1 の入口で、変えた Python の構文を試験より先に確かめ、時間切れの試行の後は再試行しない | 構文の誤りに試験の全件の時間を使わず、時間切れの繰り返しに 30 分ずつ使わない（実時間と歩留まり。旧 501・507 の残り） | Gate 1 の入口の構文の検査、時間切れの後の打ち切り、tests/test_gate_syntax_first.py | `python -m unittest tests.test_gate_syntax_first tests.test_hline_queue` | 工廠（自律） | R3-c |
| W-76 | 計画の受け入れの検査（タスクの数・依存・粒度・オラクルの出どころ）を、計画役の出力に掛ける | 実行できない粒度・粗すぎる分け方・オラクルの無いタスクを含む計画を、H ラインに渡す前に機械で止める（計画役の受け入れの基準） | harness/plan_check.py、tests/test_plan_check.py | `python -m unittest tests.test_plan_check` | 工廠（自律） | R3-c |
| W-31 | 課題 B01（玉転がし）・B05（積み木の塔）の仕様と公開の判定役を main に置く | 物理の課題を 3 本に広げ、工廠の物理の作り方が 1 本の偶然でないことを確かめる材料を置く | docs/bench/B01/spec.md、docs/bench/B05/spec.md、tests/rollgame_judge.py、tests/tower_judge.py | `python -m harness.reqcov --spec docs/bench/B01/spec.md --list && python -m harness.reqcov --spec docs/bench/B05/spec.md --list && python -c "import tests.rollgame_judge, tests.tower_judge"` | 操縦士（手動） | R3-b |
| W-32 | 課題 B01 の本体（rollgame）を工廠が仕様から作り、判定役を通す | 玉転がしの物理（傾く盤・接触・落下・ゴール）を工廠が作る | rollgame パッケージ | `python -m unittest tests.rollgame_judge` | 工廠（自律） | R3-b |
| W-33 | 課題 B05 の本体（tower）を工廠が仕様から作り、判定役を通す | 積み木の塔の物理（積み重ね・重心・崩れ）を工廠が作る | tower パッケージ | `python -m unittest tests.tower_judge` | 工廠（自律） | R3-b |
| W-22 | 横木 R3-b：課題 B06・B01・B05 の本体が、工廠の手で判定役を通す | 3 本の物理を工廠が作れたことの判定（横木 R3-b）。比べる道具に進む前提 | main の上で 3 つの判定役がそろって通ること | `python -m unittest tests.launcher_judge tests.rollgame_judge tests.tower_judge` | 操縦士（手動） | R3-b |
| W-34 | 採点役（非公開オラクルの指紋を確かめ、木を変えずに通過率を出す・床の検査） | 実装役に見えない判定（非公開オラクル）で採点する道具。チャット型との公平な比較の前提 | harness/bench_score.py、tests/test_bench_score.py | `python -m unittest tests.test_bench_score` | 工廠（自律） | R3-c |
| W-35 | 実装役に、受信箱（総監督の下書き）・非公開オラクル・生ログを読ませない（道具の設定） | 工廠が仕様だけから作ったと言えるように、総監督の下書き・参照実装・非公開オラクルを実装役の道具から外す（R3-a の B06 の参照実装は受信箱にあり、読まれ得た） | 実装役の呼び出しの禁止の列、tests/test_oracle_deny.py | python -m unittest tests.test_oracle_deny | 工廠（自律） | R3-b |
| W-36 | B06・B01・B05 の非公開オラクルを置き、指紋と床の検査の結果を main に入れる | 作った 3 本の課題を、非公開オラクルで採点できるようにする | oracles の配置、docs/bench/<id>/oracle.sha256、bench/records/<id>/floor.json（3 本） | `python -m harness.bench_score floor --task B06 --task B01 --task B05` | 操縦士（手動） | R3-c |
| W-37 | 課題 B02（倒立振子）・B03（ビリヤード）・B04（ドミノ）の仕様と非公開オラクルを置く | ベンチを 10 本にそろえる（1/3）。チャット型との差が出る難しさの課題を足す | B02〜B04 の仕様・指紋・floor.json、oracles の配置 | `python -m harness.bench_score floor --task B02 --task B03 --task B04` | 操縦士（手動） | R3-c |
| W-38 | 課題 B07（坂を登る車）・B08（ばねの鎖）の仕様と非公開オラクルを置く | ベンチを 10 本にそろえる（2/3） | B07・B08 の仕様・指紋・floor.json、oracles の配置 | `python -m harness.bench_score floor --task B07 --task B08` | 操縦士（手動） | R3-c |
| W-39 | 課題 B09（受け止め）・B10（迷路の玉）の仕様と非公開オラクルを置く | ベンチを 10 本にそろえる（3/3） | B09・B10 の仕様・指紋・floor.json、oracles の配置 | `python -m harness.bench_score floor --task B09 --task B10` | 操縦士（手動） | R3-c |
| W-40 | 進捗の記録の照合先を選べるようにする（progress complete --ref） | ベンチの走行の台帳（計画役のタスク）でも記録を照合できるようにし、W-26・W-27 の規則とベンチを両立させる | progress complete の --ref、tests/test_progress_ref.py | `python -m unittest tests.test_progress_ref` | 工廠（自律） | R3-c |
| W-41 | H ラインを別の設定ファイルで走らせる（ベンチの受信箱・統合ブランチ・base、main への PR を出さない） | 本線を止めず汚さずに、別の clone と設定で工廠を走らせる | harness.hline poll の --config、tests/test_hline_config_file.py | `python -m unittest tests.test_hline_config_file` | 工廠（自律） | R3-c |
| W-42 | 計画役の道具（課題の仕様から、What と台帳のタスクの対を書く） | 工廠の条件の起草役。仕様から What と台帳のタスクを自律で書く（C5 の本走の要件） | harness/planner.py、tests/test_planner.py | `python -m unittest tests.test_planner` | 工廠（自律） | R3-c |
| W-43 | 計画の監修のループ（計画役 → 監修役 → 差し戻しは 2 回まで → 合格で確定） | 計画役の出力を監修に通し、落ちた項目だけを直させる（要綱 3・4） | 計画の監修のループ、tests/test_planner_audit_loop.py | `python -m unittest tests.test_planner_audit_loop` | 工廠（自律） | R3-c |
| W-44 | ベンチの開始のコミット（本体と公開の判定役を除き、計画役の台帳を入れる） | 両条件を同じ出発点（本体と公開の判定役の無い土台）から始め、比較を公平にする | harness/bench_start.py、tests/test_bench_start.py | `python -m unittest tests.test_bench_start` | 工廠（自律） | R3-c |
| W-45 | チャット型の走らせ役（同じモデルの単発のセッションで解かせ、枝と採点つきの記録を残す） | 比べる相手（チャット型）を、同じモデル・同じ仕様・同じ出発点で走らせる | harness/bench_run.py の chat、tests/test_bench_run_chat.py | `python -m unittest tests.test_bench_run_chat` | 工廠（自律） | R3-c |
| W-46 | 工廠の走らせ役（監修つきの計画 → 開始のコミット → H ラインで積み切り → 採点つきの記録） | 工廠を人の手なしで 1 走行させ、記録に人の介入の有無の証跡を残す | harness/bench_run.py の factory、tests/test_bench_run_factory.py | `python -m unittest tests.test_bench_run_factory` | 工廠（自律） | R3-c |
| W-47 | 記録の検め直し（採点の再現・計画役の出力との一致・人の介入の数） | 記録の点を信じず git から採点し直し、人の介入 0 回を機械で数える | harness/bench_verify.py、tests/test_bench_verify.py | `python -m unittest tests.test_bench_verify` | 工廠（自律） | R3-c |
| W-48 | 乾式の通し：B06 をチャット型と工廠で 1 回ずつ走らせ、記録を検め直す | 道具一式を B06 の 1 本で端から端まで通し、道具の誤りを本走の前に見つける | B06 の両条件の記録と枝 | `python -m harness.bench_verify --task B06 --min-runs 1` | 操縦士（手動） | R3-c |
| W-49 | 比較の表（課題 × 条件の成功率・トークン消費・試行回数） | 課題 × 条件の成功率・トークン・試行回数を並べて見せる | harness/bench_table.py、bench/records/summary.md、tests/test_bench_table.py | `python -m unittest tests.test_bench_table` | 工廠（自律） | R3-c |
| W-50 | 天井の検査と凍結の記録 | チャット型でも満点の易しい課題で比べる失敗（過去の v2r の天井）を防ぎ、物差しを凍結する | harness/bench_ceiling.py、tests/test_bench_ceiling.py | `python -m unittest tests.test_bench_ceiling` | 工廠（自律） | R3-c |
| W-51 | 比較の測定：10 本をチャット型と工廠で 1 回ずつ走らせる | 10 本の比較の基準の値を取り、天井の検査の材料にする | 10 本 × 2 条件 × 1 回の記録、summary.md | `python -m harness.bench_verify --all --min-runs 1` | 操縦士（手動） | R3-c |
| W-23 | 横木 R3-c：天井の検査を満たして、課題 10 本・非公開オラクル・土台を凍結する | 比べる物差しの確定（横木 R3-c）。凍結の後は測定器の欠陥の修正だけを許す | bench/records/freeze.json | `python -m harness.bench_ceiling --check` | 操縦士（手動） | R3-c |
| W-52 | 2 段のブートストラップと判定（課題を束にした通過率の差の 95% 区間） | 差が偶然でないかを、課題を束にした区間で判定する | harness/bench_stats.py、tests/test_bench_stats.py | `python -m unittest tests.test_bench_stats` | 工廠（自律） | R3 |
| W-53 | 本走：凍結した 10 本をチャット型と工廠で 3 回ずつ走らせる | C5 の判定に要る走行（60 回）をそろえる | 10 本 × 2 条件 × 3 回の記録 | `python -m harness.bench_verify --all --min-runs 3 --frozen` | 操縦士（手動） | R3 |
| W-24 | 横木 R3：計画役が自律起草し監修に通った What による工廠の本走で、チャット型に有意に勝つ（C5） | 工廠がチャット型に勝つことの判定（横木 R3＝C5）。勝てる物差しがあって初めて自己改善の向きが決まる | bench/records/verdict.json、summary.md | `python -m harness.bench_stats --verdict` | 操縦士（手動） | R3 |
| W-54 | 週次の報告を自動で作る（失敗の分類の推移・ボトルネック・トークンの効率・監修の差し戻し） | Outer Loop の入力（週ごとの計器と推移）を自動で作る | harness/weekly_report.py、reports/weekly/、tests/test_weekly_report.py | `python -m unittest tests.test_weekly_report` | 工廠（自律） | R4 |
| W-55 | 課題・製品を解く What（内側）が基盤のパスを変えたら積まない | 課題を解く What が基盤を書き換えないようにし、基盤の変更を外側のループに限る（D-057・D-058） | config/foundation_paths.json、Gate 1 の検査、tests/test_foundation_guard.py | `python -m unittest tests.test_foundation_guard` | 工廠（自律） | R4 |
| W-56 | 外側の What を別の統合ブランチに積み、週に 1 本の統合 PR を出す | 基盤の改善を週次の束として、内側の積みと分けて積む | hline/outer の統合ブランチと週次の統合 PR、tests/test_hline_outer_branch.py | `python -m unittest tests.test_hline_outer_branch tests.test_hline_queue` | 工廠（自律） | R4 |
| W-57 | 非劣性の検査（外側の束でベンチの点が下がらない） | 基盤の改善でベンチの点が下がらないことを機械で確かめる（仕様ゲーミングの歯止め） | harness/bench_regress.py、tests/test_bench_regress.py | `python -m unittest tests.test_bench_regress` | 工廠（自律） | R4 |
| W-58 | 外側の統合 PR は、非劣性の検査に合格したときだけ H ラインがマージする | 非劣性に通らない基盤の改善を main に入れない | 外側の統合 PR のマージの条件、tests/test_hline_outer_merge.py | `python -m unittest tests.test_hline_outer_merge` | 工廠（自律） | R4 |
| W-59 | 外側の周期の記録の集計（報告 → 改善の What → 非劣性 → マージ → 対象の計器） | 週次の周期が回ったか・改善が計器を良くしたかを数える（C4 の判定の材料） | harness/outer_cycles.py、tests/test_outer_cycles.py | `python -m unittest tests.test_outer_cycles` | 工廠（自律） | R4 |
| W-60 | 週次の起動を登録する（総監督の週ごとのセッションと非劣性の走行） | 週次の周期を、人の促しなしに起動する | runner の週次の登録 | `python -m harness.weekly_report --latest-within-days 8` | 操縦士（手動） | R4 |
| W-61 | 外側の周期を 1 回通す（第 1 週） | Outer Loop を 1 周、端から端まで通す（外側の歩く骨格） | 第 1 週の報告・改善の What・非劣性の記録・マージ | `python -m harness.outer_cycles --min 1` | 操縦士（手動） | R4 |
| W-25 | 横木 R4：Outer Loop の週次の周期が 4 週続けて回り、構造の改善で計器が良くなり、ベンチの点が下がらない（C4） | 自己改善できる自律開発環境の確立の判定（横木 R4＝C4。最終目的） | 4 週の周期の記録 | `python -m harness.outer_cycles --min 4 --improved 1` | 操縦士（手動） | R4 |

## 一貫性の監査の手順（誰が・いつ・何で）

| 時点 | 何を確かめるか | 誰が・何で |
|---|---|---|
| 台帳にタスクを積む前 | 横木への寄与・オラクルの実在か出どころ・検証コマンドの形・この表との一致 | CI（`tests/test_ledger_double_check.py`）。通らない台帳の PR はマージできない |
| What を取り込む前 | タスクの acceptance・oracle との一致、タスクの目的と横木とのずれ、大きさ、内部の指定 | H ラインの監修役（機械 W-29・LLM W-30）。落ちた What は受信箱に残り、理由が report.md に出る |
| 積む前 | 合格（オラクル）・壊していないか | Gate 1（検証コマンド・影響テスト・変えたパッケージの全件） |
| タスクの完了の後 | 目的までの距離が進んだか・後退していないか | report.md の「## 目的までの距離」と履歴（W-63・W-64）。後退は印が付き、総監督が次の起動で最初に読む |
