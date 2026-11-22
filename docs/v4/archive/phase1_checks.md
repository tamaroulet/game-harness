# フェーズ 1 の確認の記録（0b・境界・採点の再現性・実行口の偽物の通し・deny の確認）

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
