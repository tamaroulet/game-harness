# フェーズ 1 の利用枠 0 の確かめ（0b・境界・採点の再現性・実行口の偽物の通し）

- [実験と計画](../experiments.md)の §6 から 2026-11-20 に移した記録。中身は変えていない。決めと計画は[実験と計画](../experiments.md)の §6 にある。節の番号は元のまま。

## 6-1. 0b：公式の採点に正解を通す（のうち、SWE-Game Mode 4）

- SWE-Game Mode 4（2026-11-19 の会話の席の実測。利用枠 0。ベンチのコードの変更 0）：
  - 公式の `./docker/build.sh godot --redistributable`（`gamebench-agent:godot-4.5.1`、2.09 GB）・`./docker/smoke.sh`（Godot 4.5.1、Mesa の llvmpipe で描画と PNG が通る）・`bench selfcheck`（10 項目すべて PASS）が通った。
  - 公式の `gen-task` → `eval-task`：2D の 1 ケースは生成 6 秒・採点 75 秒。3D の 1 ケースは生成 6 秒・採点 322 秒と 320 秒で、2 回の採点の全指標が一致した（WSL の 15 GiB で OOM 0）。

## 6-3. 走らせ方の境界と採点の再現性（のうち、確認）

- ネットワーク（実装役のコンテナ）：
  - 確認（実装役と同じイメージ・uid 1000 のコンテナから）：許可した 5 つは届き、github.com・raw.githubusercontent.com・api.github.com・codeload.github.com・www.google.com・huggingface.co・registry.npmjs.org と平文の HTTP はプロキシが拒む（403 Filtered）。プロキシを通らない直の接続と生の TCP は、どの宛先にも届かない。pip は PyPI からは入れられ、GitHub からは落ちる。
- 採点：
  - 確認：この形で 3 回採点して、基本のイメージは作り直されなかった（ID は `c49426ce309b…` のまま）。古い時刻（2025-06-01）では解決が変わり、採点に infrastructure_failure の印が付いた。同じ時刻の 2 回は、採点の道具の版（pytest 9.2.0・pluggy 1.7.0・プラグイン 4 つ）、結果、テストの一覧のハッシュが一致した。HOME をホストの値（/root）のまま写すと、uv が書けない場所を読んで採点が落ちる。

## 6-5. Stage 1 の実行口（のうち、偽物の agy での確かめ）

- 2026-11-20（harness/scb_run.py を作ったとき）：偽物の agy で、dev の etl_pipeline の 2 チェックポイントを H0 と H0_fresh で通した（境界の検査は通過し、検査が /tmp と ~/.cache に残したものは 0、H0 は 2 つ目で同じ会話を続け、報告されたモデルは設定と同じ、フックの SHA-256 は前後で同じ、残ったコンテナ 0）。init の前に落ちる偽物では、モデルの照合の失敗を記録してから止まった。時間切れまで眠る偽物では、それまでの stream-json が残り、フックが止めた search_web の手番（ERROR）を有効の走りと読み、コンテナの中の生きている実装役のプロセスは 5 回とも 0 になった。
- 2026-11-20（共通の層に分けた後。WSL の root）：scb_run check は BOUNDARY_OK で検査の残り 0。プロキシが同じイメージで動いていれば使い回し、止まったプロキシの作り直しは、内部のネットワークにほかのコンテナがつながっている間は拒み、いなくなってから作り直した。dev の外の名前は、置き場を作らずに止まった（終わりの符号 2）。etl_pipeline の 2 チェックポイントの H0 は、2 つ目で同じ会話を続け、どちらのターンも ok（時間切れ無し・止めた道具の走り 0）で、残ったコンテナ 0。agy の --print-timeout での止まり（result の finished_reason と、終わりの符号 3 と stderr の印）を時間切れと読むことは、単体の試験で確かめた（本物の agy ではまだ起きていない）。
