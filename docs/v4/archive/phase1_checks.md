# フェーズ 1 の利用枠 0 の確かめ（0b・境界・採点の再現性・実行口の偽物の通し）

- [実験と計画](../experiments.md)の §6 から 2026-11-20 に移した記録。中身は変えていない。決めと計画は[実験と計画](../experiments.md)の §6 にある。節の番号は元のまま。

## 6-1. 0b：公式の採点に正解を通す（のうち、SWE-Game Mode 4 と、SlopCodeBench の正解の落ちの内訳）

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

## 6-3. 走らせ方の境界と採点の再現性（のうち、確認）

- ネットワーク（実装役のコンテナ）：
  - 確認（実装役と同じイメージ・uid 1000 のコンテナから）：許可した 5 つは届き、github.com・raw.githubusercontent.com・api.github.com・codeload.github.com・www.google.com・huggingface.co・registry.npmjs.org と平文の HTTP はプロキシが拒む（403 Filtered）。プロキシを通らない直の接続と生の TCP は、どの宛先にも届かない。pip は PyPI からは入れられ、GitHub からは落ちる。
- agy のフック（2026-11-22 に移した。中身は変えていない）：
  - uid 1000 からの書き換え・消し・移しはどれも拒まれた。agy は uid 1000 で起動して、フックを読み込んだ（ログに `loaded 1 named hooks`）。
- 採点の抜け道のうち、テストの弱さの数（2026-11-22 に移した。中身は変えていない）：
    - どちらかで通るテストは、採点したテストの延べ 19,416 のうち 1,518（7.8%）。166 チェックポイントのうち 98、31 問のうち 22 にあった。
    - 群ごとには、Core 51/1,088・Error 174/982・Functionality 192/3,831・Regression 1,101/13,515。問題ごとの割合の上位 6 問は 20.6〜53.4%（dev は recli の 53.4% の 1 問。残りの 5 問は held-out で 20.6〜25.1%。held-out の問題の名前は公開に書かない）。scb-problems#39 が、空の作業場でチェックポイント 1 の 11 項目のうち 5 に通ると報告した問題は、手元でも 5/11 だった。
- 採点：
  - 確認：この形で 3 回採点して、基本のイメージは作り直されなかった（ID は `c49426ce309b…` のまま）。古い時刻（2025-06-01）では解決が変わり、採点に infrastructure_failure の印が付いた。同じ時刻の 2 回は、採点の道具の版（pytest 9.2.0・pluggy 1.7.0・プラグイン 4 つ）、結果、テストの一覧のハッシュが一致した。HOME をホストの値（/root）のまま写すと、uv が書けない場所を読んで採点が落ちる。

## 6-5. Stage 1 の実行口（のうち、偽物の agy での確かめ）

- 2026-11-20（harness/scb_run.py を作ったとき）：偽物の agy で、dev の etl_pipeline の 2 チェックポイントを H0 と H0_fresh で通した（境界の検査は通過し、検査が /tmp と ~/.cache に残したものは 0、H0 は 2 つ目で同じ会話を続け、報告されたモデルは設定と同じ、フックの SHA-256 は前後で同じ、残ったコンテナ 0）。init の前に落ちる偽物では、モデルの照合の失敗を記録してから止まった。時間切れまで眠る偽物では、それまでの stream-json が残り、フックが止めた search_web の手番（ERROR）を有効の走りと読み、コンテナの中の生きている実装役のプロセスは 5 回とも 0 になった。
- 2026-11-20（共通の層に分けた後。WSL の root）：scb_run check は BOUNDARY_OK で検査の残り 0。プロキシが同じイメージで動いていれば使い回し、止まったプロキシの作り直しは、内部のネットワークにほかのコンテナがつながっている間は拒み、いなくなってから作り直した。dev の外の名前は、置き場を作らずに止まった（終わりの符号 2）。etl_pipeline の 2 チェックポイントの H0 は、2 つ目で同じ会話を続け、どちらのターンも ok（時間切れ無し・止めた道具の走り 0）で、残ったコンテナ 0。agy の --print-timeout での止まり（result の finished_reason と、終わりの符号 3 と stderr の印）を時間切れと読むことは、単体の試験で確かめた（本物の agy ではまだ起きていない）。
- 2026-11-20（Mode 4 の実行口。WSL の root）：dev のケース 1 つ（pixel_platformer）を偽物の agy で通した。何もしない提出は、公式の何もしない対照と同じく resolved: false・0 点（complete）で、aggregate は measured: true。--print-timeout で止まる偽物（終わりの符号 3・finished_reason・stderr の印）では時間切れ（agent_timeout）と読み、提出物を作らず採点せず、aggregate は resolved: false・0 点・measured: false。起動で落ちる偽物では、モデルの照合の失敗を記録して止まった。dev の外のゲームとケースの組は、置き場を作らずに止まった。どの通しも残ったコンテナ 0。
