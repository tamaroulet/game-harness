# 頼み：L6 の前の直し 3 つ（面の関数の単位の検査・2 つ以上の走りを渡す提案役・保留の組の公開の集計を合計だけに）（クラウドの 6 本目）

このセッションの設定：モデル Opus 5.5（claude-opus-5-5）、effort high。どちらも起動の旗（--model claude-opus-5-5 --effort high）で固めた。

あなたは公開のリポジトリ tamaroulet/game-harness の写しの中で、新しい枝 1 本に変更を入れて push するところまでを行う。PR は作らない（手元の会話の席が中身を確かめてから作る）。返事は日本語。

## 0. 最初にすること（満たさなければ、何も変えずに 6 の報告だけをして終える）

1. 次を 1 回の Bash で走らせ、出力をそのまま控える。
   `date -u +%Y-%m-%dT%H:%M:%SZ; echo "session=${CLAUDE_CODE_REMOTE_SESSION_ID:-unset} effort_now=${CLAUDE_EFFORT:-unset} effort_env=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"; python3 --version; git log --oneline -1; git branch --show-current`
2. `effort_now` が `unset` でなく、`high` でもなければ終える（`unset` なら進め、報告にそう書く）。
3. あなたのモデルが Opus 5.5（claude-opus-5-5）でなければ終える。
4. 下請けのエージェント（Agent の道具）と、ネットの検索・取得の道具は使わない。
5. 開始から 2 時間を過ぎたら、その時点の変更を 5 の枝に push し、6 の報告をして終える。

## 1. 読むもの

- CLAUDE.md、docs/design/l6_approval_v3.md（全部。とくに §2・§3・§4・§6）
- harness/surface.py（全部）、harness/hline_prompt.py（全部）
- harness/proposer_launch.py（全部）と tests/test_proposer_launch.py
- harness/chain_score.py の finish_chain・finish、harness/bench_pair.py の finish・finish_pairs・passed・full、tests/test_bench_chain.py と tests/test_bench_pair.py の finish の試験

## 2. 直すこと（3 つ）

### (1) 面の差分の検査を、hline_prompt.py では関数の単位で照らす（harness/surface.py）

背景：面の 1 つ目は、`harness/hline_prompt.py` の `build_prompt` と `ledger_section` の 2 つの関数だけ（l6_approval_v3 §2）。同じファイルの `implement()`（実装役の起動・権限の拒否・ターンの上限・モデルの照合）と import は面の外。今の check はファイルの単位でしか照らさないので、面の中の差分で面の外の守りを外せる。

- check で、<old> と <new> の `harness/hline_prompt.py` を ast で読み、次を当たりにする（ファイルが変わっていなければ照らさない）。
  - 2 つの関数の外の、モジュールの頂の文（import・ほかの関数・定数・モジュールの docstring）が、並びも含めて 1 つでも違う（`ast.dump` で比べる。行の番号は比べない）。
  - 2 つの関数のどちらかが無い、名か引数（`ast.dump(node.args)`）が違う、decorator が付いた。
  - 2 つの関数の中に、次のどれかがある：Import・ImportFrom・Global・Nonlocal、名が `exec`・`eval`・`compile`・`open`・`__import__`・`setattr`・`delattr`・`getattr`・`globals`・`locals`・`vars`・`breakpoint` の呼び出し、属性の名が `write_text`・`write_bytes`・`open`・`unlink`・`rename`・`mkdir`・`rmdir`・`touch`・`chmod`・`system`・`popen`・`run`・`call`・`Popen`・`check_output`・`sleep` の呼び出し、代入・拡張代入・del の的が属性（`x.y = ...`）。
  - <new> の hline_prompt.py が無い・ast で読めない。
- 当たりの文は、どの規則に当たったかと、関数の名（か「頂の文」）と行の番号だけを出す。
- 文字列の中身（指示の言い回し）を変えるだけの差分は、当たらないこと。文字列の `replace`・`join`・`format`・`split`・`strip` などの呼び出しは当たりにしない（`replace` は Path の名の付け替えにも使われるが、文字列の置き換えでごく普通に使うので、一覧に入れない）。
- surface.py の docstring の面の説明を、2 つの関数に絞った形に直す。FRAME のパスは今のまま。

### (2) 提案役に 2 つ以上の走りを渡す（harness/proposer_launch.py）

- `--tag` を 2 回以上渡せるようにする（1 回でも今どおり動く）。同じ tag の重なりは、何もせずに終了コード 2。
- 読む根は、ハーネスの木（ROOT）と、tag ごとの `<out>/chains/<tag>/`（`--add-dir`）と、`--root` で足したもの。関所は tag ごとに記録の有無を見る（1 つでも無ければ起こさない）。oracles との重なりの関所は今どおり。
- 渡す頼みの「## 材料」に、tag の並びと、どの tag の索引も全部並べる。
- 提案の形の検めで、`## 出た段` の行（`- <土台> s<k>`）は、どれかの tag の記録に `<土台>/s<k>.json` があれば合う。
- 記録（`<out>/proposer/<stamp>.json`）の `tag` を `tags`（並び）にする。1 本だけのときも並びにする。

### (3) 保留の組の公開の集計を合計だけにする（harness/chain_score.py と harness/bench_pair.py）

背景：公開の集計（`<clone>/bench/records/chains/<tag>/<土台>.json`）は公開のリポジトリに入り、提案役がハーネスの木の中で読める。保留の組の段ごとの点が見えると、提案役が保留の組に合わせ込める（l6_approval_v3 §3）。

- 記録の set が `"dev"` でないとき（`"held"`・`"reserve"`・不明・無い）、公開の集計には段ごとの行（`stages`）を書かず、合計だけを書く：`tag`・`set`・`base`・`rev`・`stopped`（bench_pair の記録なら `from` も）と、`totals`（`stages`：採点した段の数、`passed`：通った段の数、`invalid`：無効の段の数、`tokens`：トークンの合計）。
- 「通った」は、今の bench_pair.passed と同じ決まり（無効でなく、new が全部通り、kept が全部通る）。同じ決まりを 2 か所に書かず、1 か所に置いて両方から使う（置き場は chain_score でよい。bench_pair.passed はそれを呼ぶか、それに替える）。
- 段ごとの行を含む今までの形の集計は、捨てずに、その組の非公開の置き場（chain_score.finish の priv。oracles の下）に `public_full.json` として書く。
- set が `"dev"` のときは、今までと 1 文字も変えない。
- 既存の試験のうち、保留の組の公開の集計の段ごとの行を期待しているものだけは、合計だけの形に直してよい（直した試験の名を 6 の 2 に書く）。

## 3. 守ること

- 変えてよいのは、harness/surface.py・harness/proposer_launch.py・harness/chain_score.py（(3) の分）・harness/bench_pair.py（(3) の分）、tests/ の下の新しい試験のファイル、(3) の但し書きの既存の試験だけ。config/・docs/・bench/・.github/・.claude/・harness/hline*.py・harness/bench_run.py・harness/bench_chain.py は変えない。
- 頼みに無い直しはしない。決めかねたら推して決めず、6 の 6 に書く。
- 規模：harness/ の追加は 300 行まで（試験は数えない）、関数の循環的複雑度は 15 まで。chain_score.py が 500 行を超えそうなら、(3) の合計の作り方を bench_pair 側か新しい関数に寄せ、chain_score.py の増えを小さくする（新しいモジュールは作らない）。
- 書き方は周りに合わせる（日本語の docstring とコメント、短い関数、標準ライブラリと requirements.lock にあるものだけ）。
- 試験は Windows でも通る形で書く（手元は Windows。パスは Path で比べる）。一時ディレクトリの偽のリポジトリ・記録だけで書く。claude の CLI と工廠は走らせない。課題の名前や中身を推して書かない。

足す試験（少なくとも。意地悪な場合を含める）：
1. (1)：指示の文字列だけを変える（0）・implement() の 1 行を変える（1）・import を足す（1）・頂に新しい関数や定数を足す（1）・2 つの関数の引数を変える（1）・関数の中に import / `open(` / `Path(...).write_text(` / `x.y = 1` / `subprocess.run(` / `time.sleep(` を入れる（それぞれ 1）・関数を消す（1）・壊れた Python（1）・頂の文の並びを入れ替える（1）・コメントだけを変える（0）。
2. (2)：2 つの tag で、読む根と `--add-dir` と材料の索引に両方が入る・片方の記録が無いと起こさない・重なりで 2・`## 出た段` が片方の tag にだけある段で合う・どちらにも無い段で合わない・記録の `tags`。1 つの tag の今の試験はそのまま通る。
3. (3)：held と reserve と set 無しで、公開の集計が合計だけになり、段ごとの行が無い・`passed` の数え方（無効・kept の欠け・new の欠けは通らない）・非公開の置き場に今までの形が残る・dev は今までと同じ（今の試験が通る）。bench_pair の finish でも同じ。

## 4. 確かめ

1. 依存：`python3 -m venv /tmp/venv && /tmp/venv/bin/python -m pip install -r requirements.lock`
2. 試験の命令は、毎回この形で走らせる：`PYTHONUTF8=1 HARNESS_SELFTEST_DIR=/tmp/harness-selftest /tmp/venv/bin/python -m harness.fastsuite discover -s tests`
3. 変える前に、main のままで 2 の命令を走らせ、走った件数と落ちたモジュールを控える（この箱に無い道具（dotnet など）のために落ちるものがありうる。直そうとしない）。
4. 変えた後にもう一度走らせ、落ちたモジュールが main と同じか少ないこと、足した試験が通ることを確かめる。

## 5. 枝とコミット

- 枝：`claude/l6-prep-f`（main から）。main にも、ほかの既存の枝にも push しない。この枝に push できなければ、今いる枝に push し、6 の 5 にそう書く。
- コミットは 1〜3 個（直しごとに分けてよい）。メッセージは日本語で、最後の行に `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`。

## 6. 最後の報告（この順で）

1. 0 の確かめの出力と、あなたのモデル
2. 変えたファイルごとの 1 行の要約と、足した試験の件数（直した既存の試験があれば、その名）
3. 全件の試験：main と変えた後の、走った件数と落ちたモジュール
4. 使った量：~/.claude/projects/ の下にあるこのセッションの transcript（.jsonl）の assistant の行の usage を、message の id の重なりを除いて足した input_tokens・output_tokens・cache_creation_input_tokens・cache_read_input_tokens（読めなければそう書く）と、終わりの時刻（date -u）
5. push した枝とコミットの sha
6. 決めかねたこと・守れなかったこと・読んで気づいた危うさ（無ければ「無し」）
