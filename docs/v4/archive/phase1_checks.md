# フェーズ 1 の確認の記録（0b・境界・採点の再現性・実行口の偽物の通し・deny の確認・Stage 1 の 1a と 1b）

- [実験と計画](../experiments.md)の §6 から 2026-11-20 に移した記録。中身は変えていない。決めと計画は[実験と計画](../experiments.md)の §6 にある。節の番号は元のまま。2026-11-22 に、本物の agy での deny の確認の記録（§6-5 の後半）を書き足した。

## 6-1. 0b：公式の採点に正解を通す（のうち、SWE-Game Mode 4 と、SlopCodeBench の正解の通しと落ちの内訳）

- SWE-Game Mode 4（2026-11-19 の会話の席の実測。利用枠 0。ベンチのコードの変更 0）：
  - 公式の `./docker/build.sh godot --redistributable`（`gamebench-agent:godot-4.5.1`、2.09 GB）・`./docker/smoke.sh`（Godot 4.5.1、Mesa の llvmpipe で描画と PNG が通る）・`bench selfcheck`（10 項目すべて PASS）が通った。
  - 公式の `gen-task` → `eval-task`：2D の 1 ケースは生成 6 秒・採点 75 秒。3D の 1 ケースは生成 6 秒・採点 322 秒と 320 秒で、2 回の採点の全指標が一致した（WSL の 15 GiB で OOM 0）。
- SlopCodeBench の正解の落ちの内訳（2026-11-20 の夜に、正本を 1 回で読める大きさに保つため移した。中身は変えていない）：
  - KNOWN_ISSUES.md の 8 チェックポイントのうち、手元で落ちたのは dynamic_buffer の 4 だけ（ほかの 7 は通った）。
  - KNOWN_ISSUES.md の外で落ちたのは 4 問で、どれも外部の監査（slop-code-bench#27）が公式のイメージで報告したものと同じ：

| 問題 | 落ちたチェックポイント | 型 |
|:--|:--|:--|
| test_translator | 1〜4（5〜8 は止めた）。TypeScript の項目だけ | 環境：公式のイメージに ts-node が無く、正解が使う `npx ts-node --esm` が最新の typescript を取って組み合わずに落ちる。公式の設定では直らない |
| dynamic_buffer | 2・3（4 は KNOWN_ISSUES） | 正解のコードの生成の誤り |
| env_manager | 3（前のチェックポイントのテスト 3 項目） | 正解が前の振る舞いを壊す |
| pwd_manager | 4（前のチェックポイントのテスト 1 項目） | 不安定（監査は走りごとに違う 1 項目が落ちると報告） |

- SlopCodeBench の走らせ方と正解の通し（2026-11-22 に、正本を 1 回で読める大きさに保つため移した。中身は変えていない）：
  - 走らせ方の条件のうち：WSL の root から起動した最初の走りは、権限の検査が正解でも落ちたので使わない。
  - 公式の正解を、公式の `slop-code eval-snapshot` に 192 チェックポイント通した（196 のうち、test_translator の 5〜8 はチェックポイント 4 が 778 秒かかったので止めた）。全テストに通ったのは 183。採点の時間は 1 チェックポイントの中央 41 秒、上位 1 割 128 秒以上、最大 753 秒。メモリの最大は 4.4 GB（4 並列）。最後まで走った問題はコンテナを残さなかった。

## 6-3. 走らせ方の境界と採点の再現性（のうち、確認）

- ネットワーク（実装役のコンテナ）：
  - 確認（実装役と同じイメージ・uid 1000 のコンテナから）：許可した 5 つは届き、github.com・raw.githubusercontent.com・api.github.com・codeload.github.com・www.google.com・huggingface.co・registry.npmjs.org と平文の HTTP はプロキシが拒む（403 Filtered）。プロキシを通らない直の接続と生の TCP は、どの宛先にも届かない。pip は PyPI からは入れられ、GitHub からは落ちる。
  - PyPI に SlopCodeBench の問題やテストを含むパッケージが無いことを確認した（作者の scb-check は品質の指標の道具）。（2026-11-22 に移した。中身は変えていない）
- agy のフック（2026-11-22 に移した。中身は変えていない）：
  - uid 1000 からの書き換え・消し・移しはどれも拒まれた。agy は uid 1000 で起動して、フックを読み込んだ（ログに `loaded 1 named hooks`）。
- 採点の抜け道のうち、テストの弱さの数（2026-11-22 に移した。中身は変えていない）：
    - どちらかで通るテストは、採点したテストの延べ 19,416 のうち 1,518（7.8%）。166 チェックポイントのうち 98、31 問のうち 22 にあった。
    - 群ごとには、Core 51/1,088・Error 174/982・Functionality 192/3,831・Regression 1,101/13,515。問題ごとの割合の上位 6 問は 20.6〜53.4%（dev は recli の 53.4% の 1 問。残りの 5 問は held-out で 20.6〜25.1%。held-out の問題の名前は公開に書かない）。scb-problems#39 が、空の作業場でチェックポイント 1 の 11 項目のうち 5 に通ると報告した問題は、手元でも 5/11 だった。
- 採点：
  - 確認：この形で 3 回採点して、基本のイメージは作り直されなかった（ID は `c49426ce309b…` のまま）。古い時刻（2025-06-01）では解決が変わり、採点に infrastructure_failure の印が付いた。同じ時刻の 2 回は、採点の道具の版（pytest 9.2.0・pluggy 1.7.0・プラグイン 4 つ）、結果、テストの一覧のハッシュが一致した。HOME をホストの値（/root）のまま写すと、uv が書けない場所を読んで採点が落ちる。
  - 採点ごとに environment.env を変える形がイメージの固定とぶつかることの確認（2026-11-22 に移した。中身は変えていない）：確認の採点で実際に作り直しが起き、公式の設定の採点がビルドのキャッシュから同じ ID を作り直して名前を戻した。
  - 採点の前の基本のイメージの ID の照らし合わせ（2026-11-22 に移した。中身は変えていない）：偽物の通しで、確認の採点が付け替えた名前を、この照らし合わせが採点の前に止めた。

## 6-5. Stage 1 の実行口（のうち、偽物の agy での確かめ）

- 2026-11-20（harness/scb_run.py を作ったとき）：偽物の agy で、dev の etl_pipeline の 2 チェックポイントを H0 と H0_fresh で通した（境界の検査は通過し、検査が /tmp と ~/.cache に残したものは 0、H0 は 2 つ目で同じ会話を続け、報告されたモデルは設定と同じ、フックの SHA-256 は前後で同じ、残ったコンテナ 0）。init の前に落ちる偽物では、モデルの照合の失敗を記録してから止まった。時間切れまで眠る偽物では、それまでの stream-json が残り、フックが止めた search_web の手番（ERROR）を有効の走りと読み、コンテナの中の生きている実装役のプロセスは 5 回とも 0 になった。
- 2026-11-20（共通の層に分けた後。WSL の root）：scb_run check は BOUNDARY_OK で検査の残り 0。プロキシが同じイメージで動いていれば使い回し、止まったプロキシの作り直しは、内部のネットワークにほかのコンテナがつながっている間は拒み、いなくなってから作り直した。dev の外の名前は、置き場を作らずに止まった（終わりの符号 2）。etl_pipeline の 2 チェックポイントの H0 は、2 つ目で同じ会話を続け、どちらのターンも ok（時間切れ無し・止めた道具の走り 0）で、残ったコンテナ 0。agy の --print-timeout での止まり（result の finished_reason と、終わりの符号 3 と stderr の印）を時間切れと読むことは、単体の試験で確かめた（本物の agy ではまだ起きていない）。
- 2026-11-20（Mode 4 の実行口。WSL の root）：dev のケース 1 つ（pixel_platformer）を偽物の agy で通した。何もしない提出は、公式の何もしない対照と同じく resolved: false・0 点（complete）で、aggregate は measured: true。--print-timeout で止まる偽物（終わりの符号 3・finished_reason・stderr の印）では時間切れ（agent_timeout）と読み、提出物を作らず採点せず、aggregate は resolved: false・0 点・measured: false。起動で落ちる偽物では、モデルの照合の失敗を記録して止まった。dev の外のゲームとケースの組は、置き場を作らずに止まった。どの通しも残ったコンテナ 0。

## 6-5. Stage 1 の deny の確認（本物の agy。2026-11-22 に書いた記録）

- 走り：2026-11-22 12:17:02〜12:17:33（31 秒。agy のターン 15.9 秒）。main 450be52 の写し・固定の agy 1.5.3（実行ファイルの SHA-512 を照合）・gemini-4-hydrogen-medium（報告と一致）。フックの SHA-256 は 09f90f78f432…（前後で同じ）、境界の検査は通過、残ったコンテナ 0、作業場のファイル 0。頼んだ道具は、当時の既定の search_web・read_url_content・read_browser_page・invoke_subagent で、当時の判定は「呼ばれなかった: read_url_content,read_browser_page」で落ち。
- search_web：1 回（引数 query "SlopCodeBench"）。ステップは ERROR（0.28 秒）で、`tool_info.error` の文は「tool call denied by pre-tool hook: Web access is not available in this environment.」。
- invoke_subagent：1 回（引数 Role "Permission Check Subagent"・TypeName "self"）。ACTIVE のイベントは step_type tool（tool_info あり）。ERROR のイベント（0.30 秒）は step_type subagent・`subagent_info.subagents` が空で、tool_info もエラーの文も無い。モデルの返事は「Encountered error in tool execution: tool call denied by pre-tool hook: Subagents are not available in this environment.」と引用した（deny_subagent.sh の理由の文と同じ。同じ返事の search_web の行の引用は、stream の文と一致する）。
- read_url_content・read_browser_page：呼ばれなかった。モデルの返事は「not called — tool is not declared/available in this environment's toolset.」。ほかの道具は呼ばれなかった。
- モデルに渡る道具の根拠（利用枠 0。固定の 1.5.3 の実行ファイルの文字列を読んだ）：実行ファイルには、CLI の側の道具の組（ソースの置き場 `third_party/oneharness/internal/tools/`。ask_question・define_subagent・edit_file・finish・generate_image・invoke_subagent・manage_subagents・manage_task・read_file・run_command・schedule・search_web・send_message・write_file の 14 と、provider_tool）と、IDE の側（`third_party/jetski/cortex/`。view_file・read_url_content の処理やブラウザの部品があり、init の 60 の名前はこちらの名前と見られる）がある。走りに出た道具の名前は、どれも CLI の側の名前（1a は run_command・write_file で、init の一覧には write_to_file はあるが write_file は無い。この確認は search_web・invoke_subagent）。read_url_content と read_browser_page は CLI の側の組に無い。agy は、モデルに渡す道具の一覧を出さない。
- トークン：キャッシュしない入力 12,773・キャッシュの読み 11,883・出力 1,066（うち thinking 865）。モデルのステップ 3 つで、1 つ目の入力 7,755・キャッシュ 0、2 つ目から当たる。
- 操縦士の決め（2026-11-22）：この記録で、本物の agy のフックが Web の道具と下位エージェントの両方を止めることは確認できたとし、走らせ直さない。確認の道具を search_web・invoke_subagent の 2 つにし、下位エージェントのステップの印（ERROR・下位エージェント 0・返事の deny の文の引用）を判定に入れた。新しい判定をこの記録に当てると合格する。
- 実験と計画 §6-5 の deny の確認の形（2026-11-22 に移した。リンクのパスのほかは変えていない）：deny の確認（harness/scb_run.py の deny-check。比べの記録に入れない）：新しい会話の 1 ターンで、今の CLI がモデルに渡す止めた道具 `search_web`・`invoke_subagent` を 1 度ずつ呼ぶよう頼み、どのステップもフックの deny の印（`tool_info.error` の文か、下位エージェントのステップでは ERROR・下位エージェント 0・返事の deny の文の引用）で止まれば合格。2026-11-22 の 1 回はモデルに渡らない 2 つも頼んで決めの線では落ちたが、`search_web`・`invoke_subagent` がフックで止まったことは記録に残り、フックは変えないので走らせ直さない（操縦士の決め、2026-11-22。記録は [phase1_checks.md](phase1_checks.md) の §6-5）。

## 6-5. Stage 1 の 1a と 1b（本物の agy。2026-11-22 に書いた記録）

- 走らせ方：固定の agy 1.5.3（実行ファイルの SHA-512 を走りのたびに照合）・gemini-4-hydrogen-medium（どのチェックポイントも報告と一致）・フックの SHA-256 は 09f90f78f432…（前後で同じ）・1 チェックポイントの時間の上限 3600 秒・採点の基本のイメージの ID は固定の値（c49426ce309b…）。
- 見込みの立て直しの手順（実験と計画 §6-5 から 2026-11-22 に移した。中身は変えていない）：1a の後と 1b の後に、1 チェックポイントの週の減りと壁時計の実測を報告し、それで 1b と 1c の費用を見込み直して、確認を取ってから次を走らせる。見込みの合計が、リセットの後に下限まで使える分（65 ポイント）の 6 割（上限の目安）を超えたら、1c を縮める案（sith を外す・H0 だけにする）を添える。
- 1a：2026-11-22 10:53:35〜10:56:06（151 秒。実装役 98.1 秒、採点 6.5 秒）。main f5558cc の写し。etl_pipeline のチェックポイント 1 を H0 で 1 回。41/41（strict・isolated・core とも合格）。時間切れ無し・止めた道具の呼び出し 0・残ったコンテナ 0。トークンは、キャッシュしない入力 31,516・キャッシュの読み 82,468・出力 16,093（うち思考 8,061）。1 ステップ目のキャッシュしない入力は 8,628・キャッシュ 0 で、2 ステップ目から当たった。実装役は単体テストを 12 件書いて走らせてから終えた。利用枠の表示は、直後に週 −0.71・5 時間 −0.57 ポイント、20 分後には走る前より +0.81・+0.99 ポイント（設計 §12 の M-05 の動き。費用はトークンで読む）。
- 1b：main 4d19d1e の写し（116 ファイルの SHA-256 が一致）。etl_pipeline の全 5 チェックポイントを H0（13:27:39〜13:37:08、569 秒）と H0_fresh（13:37:52〜13:54:10、978 秒）。10 チェックポイントとも有効・採点の infrastructure_failure 無し・時間切れ 0・残ったコンテナ 0。止める線 1〜9 は当たらなかった。H0 は 2〜5 チェックポイントで同じ会話を続けた。H0_fresh は 5 回とも新しいコンテナで、境界の検査は 5 回とも通過した。

| | H0 | H0_fresh | H0_fresh ÷ H0 |
|:--|--:|--:|--:|
| strict・isolated・core | 2・4・5 | 0・3・5 | |
| チェックポイント 5 のテスト | 163/164 | 162/164 | |
| 前に通っていたテストの退行 | 0 | 0 | |
| キャッシュしない入力 | 120,356 | 303,156 | 2.52 |
| キャッシュの読み | 1,550,904 | 1,820,945 | 1.17 |
| 出力（うち思考） | 57,734（19,683） | 93,821（44,405） | 1.63 |
| キャッシュの当たり | 92.8% | 85.7% | |
| モデルのステップ | 32 | 82 | 2.56 |
| 実装役の時間 | 348.3 秒 | 733.0 秒 | 2.10 |
| 採点の時間 | 77.5 秒 | 76.9 秒 | 0.99 |
| 走り全体の壁時計 | 569 秒 | 978 秒 | 1.72 |

- 落ちたテスト（どちらも、実装役が自分で書いたテストは通っていた）：
  - チェックポイント 3 の test_branch_empty_branches_array_error（H0 と H0_fresh の両方。後のチェックポイントでも落ちたまま）：仕様は branches を「non-empty array」とし、規則に反したら MALFORMED_STEP を返し、前のチェックポイントのスキーマの検査（SCHEMA_VALIDATION_FAILED）も当たるとするが、空の配列のエラーコードは書いていない。実装役は SCHEMA_VALIDATION_FAILED を返し、テストは MALFORMED_STEP を求めた。
  - チェックポイント 1 の test_rename_missing_column_error（H0_fresh だけ。H0 は同じ文面で通った）：エラーコードの一覧に MISSING_COLUMN があるが、返す条件は書いていない。実装役はデータに無い列を指す rename を成功で返し、テストは失敗を求めた。
- 新しい会話の 1 ステップ目のキャッシュしない入力（キャッシュ 0。どれも 2 ステップ目から当たり、その後の当たらないステップは 0）：H0_fresh の 5 回は 8,622・8,902・8,505・8,446・8,752、H0 のチェックポイント 1 は 8,626、1a は 8,628（文面が同じ 7 回で平均 8,640）。H0 の 2〜5 チェックポイントの 1 ステップ目は、キャッシュしない入力が 2,114〜3,870 で、前の会話のキャッシュに 23,008〜78,096 当たった。H0_fresh と H0 のキャッシュしない入力の差 182,800 のうち、2〜5 チェックポイントの 1 ステップ目の差は 22,774（12%）。
- 道具：run_command・write_file・edit_file・read_file・manage_task（どれも CLI の側の組の名前）。manage_task は H0_fresh のチェックポイント 5 で 1 回で、裏に回った単体テストのコマンドの状態を見ただけ。generate_image・schedule は出なかった。
- 利用枠：10 走りで週 1.9〜2.6 ポイント（走る前の読みが 0.9606〜0.9676 と揺れたので幅で書く。1 チェックポイントあたり 0.19〜0.26）。
