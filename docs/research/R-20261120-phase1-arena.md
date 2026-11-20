# フェーズ 1 の土俵（SlopCodeBench と SWE-Game Mode 4）と、走らせ方の境界

- 記録：R-20261120-phase1-arena
- 段：compare
- 決めたい判断：フェーズ 1 の土俵を、一般の連続積み上げ・非退行は SlopCodeBench、ゲーム骨子は SWE-Game Mode 4 にしてよいか。母集団から外す問題と、走らせ方の境界（ネットワーク・採点の再現性・採点の抜け道）を何で決めるか
- 調べた日時：2026-11-20T16:03:07+09:00
- 調べ役：seat（）
- 照合：verified 21・quote_not_found 0・unreachable 0・no_source 0
- 印：verified＝抜粋が出典の本文にある。quote_not_found＝ページはあるが抜粋が無い。unreachable＝取りに行けない（偽ではなく未照合）。no_source＝出典が無い

## 主張

| ID | 主張 | 系統 | 分野 | 格 | 種類 | 公開日 | 印 | 出典 |
|:--|:--|:--|:--|:--|:--|:--|:--|:--|
| C1 | 各チェックポイントは、新しい Docker のコンテナで非 root のユーザーとして走り、チェックポイントの間に残るのは作業のフォルダだけ。 | SlopCodeBench の公式の形 | academia | primary | fact | 2026-03 | verified | [SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon Iterative Tasks（arXiv 2603.24755、HTML）](https://arxiv.org/html/2603.24755) |
| C2 | エージェントの作業場（自分のコード）は次のチェックポイントへ引き継がれ、品質は毎ステップで測る。 | SlopCodeBench の公式の形 | academia | primary | fact | 2026-03 | verified | [SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon Iterative Tasks（arXiv 2603.24755、HTML）](https://arxiv.org/html/2603.24755) |
| C3 | 前のチェックポイントのテスト（regression）が前の要求を後へ運ぶので、前のチェックポイントの誤りが後のチェックポイントの点を 0 にしうる。 | SlopCodeBench の公式の形 | academia | primary | fact | 2026-03 | verified | [SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon Iterative Tasks（arXiv 2603.24755、HTML）](https://arxiv.org/html/2603.24755) |
| C4 | どのエージェントも 36 問のどれも全部は解いていない。 | SlopCodeBench の公式の形 | academia | primary | fact | 2026-03 | verified | [SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon Iterative Tasks（arXiv 2603.24755、HTML）](https://arxiv.org/html/2603.24755) |
| C5 | strict（全テストの通過）の最高は GPT 5.5 の 14.8%。 | SlopCodeBench の公式の形 | academia | primary | fact | 2026-03 | verified | [SlopCodeBench: Benchmarking How Coding Agents Degrade Over Long-Horizon Iterative Tasks（arXiv 2603.24755、HTML）](https://arxiv.org/html/2603.24755) |
| C6 | 公式の既知の問題の一覧は、仕様の変化のため、正解の一部はすべてのテストには通らないと書いている。 | 正解とテストの欠陥 | standards_tools | primary | fact | 不明 | verified | [SlopCodeBench docs/KNOWN_ISSUES.md（コミット de70a479acce）](https://raw.githubusercontent.com/SprocketLab/slop-code-bench/de70a479acce7b565d2d0c772f48d7c881c15510/docs/KNOWN_ISSUES.md) |
| C7 | コンテナは、それを起動したホストのプロセスの uid:gid で走る（環境変数と環境の設定で上書きできる）。 | SlopCodeBench の公式の形 | standards_tools | primary | fact | 不明 | verified | [SlopCodeBench docs/execution/docker.md（コミット de70a479acce）](https://raw.githubusercontent.com/SprocketLab/slop-code-bench/de70a479acce7b565d2d0c772f48d7c881c15510/docs/execution/docker.md) |
| C8 | 外部の監査は、公式のイメージで dynamic_buffer のチェックポイント 2〜4 の正解が自分のテストに落ちると報告した。 | 正解とテストの欠陥 | practitioners | secondary | fact | 2026-08-11 | verified | [slop-code-bench#27 Pinned answer keys fail evaluation in the official image](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/27) |
| C9 | 同じ監査は、env_manager のチェックポイント 3 の正解が前のチェックポイントのテスト 3 つに落ちると報告した。 | 正解とテストの欠陥 | practitioners | secondary | fact | 2026-08-11 | verified | [slop-code-bench#27 Pinned answer keys fail evaluation in the official image](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/27) |
| C10 | 同じ監査は、pwd_manager のチェックポイント 4 が不安定（走りごとに違う 1 項目が落ちる）と報告した。 | 正解とテストの欠陥 | practitioners | secondary | fact | 2026-08-11 | verified | [slop-code-bench#27 Pinned answer keys fail evaluation in the official image](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/27) |
| C11 | 同じ監査は、test_translator の TypeScript の 8 チェックポイントが、npx が最新の typescript を取ると全部落ちると報告した。 | 正解とテストの欠陥 | practitioners | secondary | fact | 2026-08-11 | verified | [slop-code-bench#27 Pinned answer keys fail evaluation in the official image](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/27) |
| C12 | meshctl のテストは、提出物から 3 つの名前だけを作業場に写すので、複数のファイルに分けた解は import で落ちる。 | 正解とテストの欠陥 | practitioners | secondary | fact | 2026-10-27 | verified | [scb-problems#37 meshctl: test workspace copies only meshctl.py](https://api.github.com/repos/gabeorlanski/scb-problems/issues/37) |
| C13 | 作業場の根に実装役が置いた conftest.py が採点を 0 にする問題を、採点のテストのフォルダで conftest の探索を止めて直した（マージ済み）。 | テストの漏れと抜け道 | standards_tools | primary | fact | 2026-06-10 | verified | [slop-code-bench#16 Isolate eval pytest collection from agent-authored conftest.py](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/16) |
| C14 | cursor-agent が Web で SlopCodeBench の隠しテストを引いた例が報告されている。 | テストの漏れと抜け道 | practitioners | secondary | fact | 2026-08-03 | verified | [slop-code-bench#24 Network access](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/24) |
| C15 | 外部の追試（DeepSeek V4.1 Flash、各 1 回）で、続く会話の回は core 134/196、strict 44/196（22.4%）だった。 | 続く会話と新しい会話 | practitioners | secondary | fact | 2026-10-16 | verified | [slop-code-bench#45 Closing report: our four rounds on the official setup](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/45) |
| C16 | 同じ追試で、チェックポイントごとに新しいプロセスと会話にした回は core 144/196、strict 66/196（33.7%）だった。 | 続く会話と新しい会話 | practitioners | secondary | fact | 2026-10-16 | verified | [slop-code-bench#45 Closing report: our four rounds on the official setup](https://api.github.com/repos/SprocketLab/slop-code-bench/issues/45) |
| C17 | scb-problems#39 が挙げた 1 問では、空の作業場がチェックポイント 1 の 11 項目のうち 5 に通る（エラーのテストが終わりの符号だけを見る）。 | テストの漏れと抜け道 | practitioners | secondary | fact | 2026-11-20 | verified | [scb-problems#39 Grader bypasses and verifier gaps in SlopCodeBench tasks](https://api.github.com/repos/gabeorlanski/scb-problems/issues/39) |
| C18 | scb-problems#39 が挙げた別の 1 問のチェックポイント 4 では、何も書かずに 0 で終わるプログラムが core の 9 項目のうち 8 に通る（期待の出力が実行のフォルダに先に置かれている）。 | テストの漏れと抜け道 | practitioners | secondary | fact | 2026-11-20 | verified | [scb-problems#39 Grader bypasses and verifier gaps in SlopCodeBench tasks](https://api.github.com/repos/gabeorlanski/scb-problems/issues/39) |
| C19 | SWE-Game の Bug Repair は、ゲームの振る舞いを保ったまま修理する課題で、入力は不具合のあるプロジェクト・遊びの要件・不具合の報告。 | SWE-Game の Bug Repair | standards_tools | primary | fact | 不明 | verified | [SWE-Game README（コミット a1de4d63f750）](https://raw.githubusercontent.com/Charly-chan/SWE-Game/a1de4d63f750bcc70cbccc8aca0ad672ef189cca/README.md) |
| C20 | SWE-Game の同梱の作品には、別の条件が記録されたものがある（例：Hurry Curry の上流の素材は AGPL-3.0-only）。 | SWE-Game の Bug Repair | standards_tools | primary | fact | 不明 | verified | [SWE-Game THIRD_PARTY_NOTICES.md（コミット a1de4d63f750）](https://raw.githubusercontent.com/Charly-chan/SWE-Game/a1de4d63f750bcc70cbccc8aca0ad672ef189cca/THIRD_PARTY_NOTICES.md) |
| C21 | uv は UV_EXCLUDE_NEWER を与えると、その日時より後に公開された配布物を候補から外す。 | 採点の依存の再現性 | standards_tools | primary | fact | 不明 | verified | [uv の環境変数の文書](https://docs.astral.sh/uv/reference/environment/) |

## 抜粋

- C1：「Each checkpoint runs in a fresh Docker container as a non-root user; only the working directory persists between checkpoints」
- C2：「workspace is carried forward to the next checkpoint, and quality is measured at every step」
- C3：「Because regression tests carry earlier requirements forward」
- C4：「No agent fully solves any of the 36 problems」
- C5：「GPT 5.5 achieves the highest strict solve rate at 14.8」
- C6：「some reference solutions may not pass all tests」
- C7：「Containers run as the uid:gid of the host process that launched them」
- C8：「checkpoints 2 through 4 fail their own tests (48/50, 102/104, 122/172)」
- C9：「checkpoint 3 fails three regression tests from checkpoints 1 and 2 (184/187)」
- C10：「checkpoint 4 is unstable」
- C11：「All eight TypeScript checkpoints fail when」
- C12：「copies only three names from the submission」
- C13：「caps conftest discovery at the eval test dir」
- C14：「just looked up the SlopCodeBench hidden tests on the web」
- C15：「continuous session — the run in #40 ／ 36/36 ／ 134/196 (68.4%) ／ 44/196 (22.4%)」
- C16：「fresh process + fresh session per checkpoint, no cross-checkpoint instruction channel ／ 36/36 ／ 144/196 (73.5%) ／ 66/196 (33.7%)」
- C17：「passes 5 of the 11 checkpoint 1 tests」
- C18：「exits 0 without writing anything therefore passes 8 of the 9 core case tests」
- C19：「Repair a game while preserving its behavior」
- C20：「Hurry Curry upstream material ／ AGPL-3.0-only」
- C21：「If set, uv will exclude distributions published after the specified date」

## 確かめられなかったこと

- agy（Antigravity CLI 1.5.3）の hooks.json の仕様は、公開の URL ではなく配布物に埋め込まれた文書で読んだ（実験と計画 §6-3。ここでは主張にしない）
- PreToolUse の deny が search_web を実際に止めるかは、モデルの呼び出しが要るので未確認（Stage 1 の軌跡で確認する）
- issue #45 の追試は外部の 1 チームの各 1 回で、続く会話と新しい会話のほかにも条件が違う

## 検索語

- slop-code-bench issues（GitHub の API で全件を一覧した）
- scb-problems issues（同）
- SlopCodeBench paper arXiv 2603.24755
- uv UV_EXCLUDE_NEWER

## 問い

# フェーズ 1 の土俵（SlopCodeBench と SWE-Game Mode 4）と、走らせ方の境界

## 段
compare

## 決めたい判断
フェーズ 1 の土俵を、一般の連続積み上げ・非退行は SlopCodeBench、ゲーム骨子は SWE-Game Mode 4 にしてよいか。母集団から外す問題と、走らせ方の境界（ネットワーク・採点の再現性・採点の抜け道）を何で決めるか

## 判断を変えうるもの
公式の正解が手元で広く落ちる。テストの弱さや抜け道が採点を左右する。隠しテストを Web で引ける道を塞げない。採点の依存の解決が日によって変わり、採り直せない

## 候補
SlopCodeBench・SWE-Chain・SWE-Game Mode 4

## 問い
SlopCodeBench の公式の形（チェックポイントごとのコンテナ・作業場の引き継ぎ・前のチェックポイントのテスト・難しさ）、公式と外部の監査が挙げた正解とテストの欠陥、ネットワークとテストの漏れ、続く会話と新しい会話の外部の観測、SWE-Game の Bug Repair の形とライセンスの注記、採点の依存の解決を日付で固定する手段を、一次の出典で確認する。SWE-Chain と SlopCodeBench の手元の実測（0b）は、実験と計画 §6-1 に書く（この記録の外）。
