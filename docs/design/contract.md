# 契約ファイル `.harness.toml` の設計（Step 1）

- 状態: 設計のみ。コードは変更していない
- 前提: harness `77d84a3`、unity-2d `f3e4dbb`
- 決定（2026-09-17、人間）: 形式は TOML（`tomllib`）。main から読む。門で変更を禁止する

## 1. 目的と、何を守るのか

ゲームごとの違いを、harness のコードからゲームのリポジトリへ移す。ただし次の 3 点は崩さない。

1. **実装役は契約に触れない。** 契約は、保護された既定ブランチの先頭からだけ読む。サンドボックス・作業ツリー・PR の head からは読まない
2. **契約で門を緩められない。** harness 側に組み込んだ禁止は、契約から外せない。契約にできるのは「足す」ことだけ
3. **読めなければ止まる（フェイルクローズド）。** 無い・壊れている・知らないキーがある・型が違う、のいずれかなら ABORT する。既定値に落として続行しない

## 2. 設定の 4 層

| 層 | 置き場所 | 中身 | 誰が変えるか |
|:--|:--|:--|:--|
| **契約** | ゲームのリポジトリ直下 `.harness.toml` | このゲームの規則（パス、許可・禁止、テストの種類、オラクル、上限、必須チェック、プレイ確認） | 人間が承認した PR だけ |
| **登録** | harness `projects/<id>/project.json` | 契約を取りに行くための最小限（id、GitHub のリポジトリ、既定ブランチ） | harness の変更 |
| **共通設定** | harness `config/*.json` | 全ゲーム共通の運用（ラベル、再試行、分解役・監査役・実装役の起動、既定の TTL） | harness の変更 |
| **マシン設定** | `C:\src\.local\harness\machine.toml`（コミットしない。雛形は `config/machine.example.toml`） | この PC でのパス（リポジトリ、サンドボックス、出力先、Unity Editor の実体、ロック） | その PC で人間 |

**マシン設定をリポジトリに入れない理由**: Unity のパスや作業場所は PC ごとに違う。リポジトリに書くと、PC を変えるたびに承認つきの PR が要り、しかも他人の PC の値が混ざる。

## 3. `.harness.toml` スキーマ（schema = 1）

表の見方: **必須** = 無ければ ABORT。任意のキーは既定値を書いた。**知らないキーは ABORT**（綴り間違いが「黙って緩む」原因になるため）。
パスはすべてリポジトリ直下からの相対で、区切りは `/`。絶対パスと `..` を含むものは ABORT。

### 3.1 最上位

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `schema` | int | 必須 | — | `1` 以外は ABORT |
| `engine.kind` | str | 必須 | — | エンジン別アダプタの選択。v1 は `"unity"` だけ |

`engine` を最上位の文字列にすると、`[engine.unity]` の表と名前が衝突して TOML として読めない（`tomllib` で実測）。そのため `[engine]` 表の `kind` にする。

### 3.2 `[paths]`

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `impl_roots` | list[str] | 必須 | — | Pure C# 実装の置き場（分解役に渡す）。各要素は `permitted` のどれかに含まれていること |
| `test_dir` | str | 必須 | — | 受入テストの置き場。**自動的に禁止に加わる** |
| `units_dir` | str | 必須 | — | 単位定義の置き場。自動的に禁止に加わる |
| `audit_reports_dir` | str | 任意 | `"reports/audits"` | 監査レポートの置き場。自動的に禁止に加わる |
| `permitted` | list[glob] | 必須 | — | 実装役が変更してよい範囲の**上限** |
| `prohibited` | list[glob] | 任意 | `[]` | 追加の禁止。組み込みの禁止（§5.2）に**足される** |

### 3.3 `[engine.unity]`（`engine.kind = "unity"` のとき必須）

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `project_subdir` | str | 必須 | — | Unity プロジェクトの場所（例 `"Game"`） |
| `editor_version` | str | 必須 | — | 例 `"6000.3.23f1"`。マシン設定の `[unity.editors]` で実体のパスを引く |
| `test_platform` | str | 任意 | `"EditMode"` | `EditMode` / `PlayMode` |
| `skipped_baseline` | int | 任意 | `0` | 既知の skip 件数（旧 `unity_skip_baseline`） |
| `golden_dir_env_vars` | list[str] | 任意 | `[]` | ゴールデンの置き場を Unity テストに渡す環境変数名 |

### 3.4 `[tests.fast]` と `[tests.engine]`

**テストは「コマンド文字列」ではなく「ランナー ID」で指定する。** 実際の引数はアダプタが組み立てる。
v1 では、契約に任意のシェルコマンドを書く道を作らない（main が保護されていても、実行経路は少ないほうがよい）。

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `tests.fast.runner` | str | 必須 | — | v1 は `"dotnet"` |
| `tests.fast.project` | str | 必須 | — | 例 `"tests/Core.Tests/Core.Tests.csproj"` |
| `tests.fast.result_format` | str | 任意 | ランナーの既定（dotnet は `"trx"`） | 結果ファイルの形式 |
| `tests.engine.runner` | str | 必須 | — | v1 は `"unity-testrunner"` |
| `tests.engine.result_format` | str | 任意 | `"nunit3"` | |
| `tests.*.ttl_seconds` | int | 任意 | 共通設定の既定 | プロジェクトの規模で変わるので契約で上書きできる |

### 3.5 `[oracle]`

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `control_must_pass` | str | 必須 | — | 必ず通る対照群のテスト名 |
| `control_must_fail` | str | 必須 | — | 必ず落ちる対照群のテスト名（毒饅頭） |
| `quarantine` | list[table] | 任意 | `[]` | P2P から外す不安定テスト。各要素 `{ test, reason, approved_in }`。**`approved_in`（PR 番号）の無い要素は ABORT** |

### 3.6 `[limits]` と `[static]`

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `limits.max_impl_lines` | int | 必須 | — | 差分の上限（追加 + 削除。未追跡を含む） |
| `static.forbidden_leftover` | str | 任意 | `"NotImplementedException"` | |
| `static.forbidden_skip_attribute_regex` | str | 任意 | `'\[\s*(Ignore\|Explicit)\b'` | |
| `static.selftest_forbidden_probe` | str | 必須（`forbidden` があるとき） | — | 自己検査で禁止パターンの発火を確かめる literal |
| `[[static.forbidden]]` | array of table | 任意 | `[]` | `{ pattern, label, applies_to = impl_roots }`。**単位定義で足すことはできるが、消すことはできない** |

### 3.7 `[merge]` と `[playtest]`

| キー | 型 | 必須 | 既定 | 説明 |
|:--|:--|:--|:--|:--|
| `merge.required_checks` | list[str] | 必須 | — | **`"approval"` を含まなければ ABORT**（変異 M28 で、ここが抜けると人間の承認が素通りになることを実測済み） |
| `playtest.scene` | str | 任意 | — | 起動シーン。自動的に禁止に加わる |
| `playtest.build_method` | str | 任意 | — | `-executeMethod` に渡す非破壊のビルド関数 |

### 3.8 unity-2d の場合（移行後の見本）

```toml
schema = 1

[engine]
kind = "unity"

[paths]
impl_roots = ["Game/Assets/Core"]
test_dir = "tests/Core.Tests"
units_dir = "tools/units"
audit_reports_dir = "reports/audits"
permitted = [
  "Game/Assets/Core/**/*.cs",
  "Game/Assets/Features/**/*.cs",
]
prohibited = [
  "Game/Assets/Tests/**",
  "Game/ProjectSettings/**",
  "Game/Packages/**",
]

[engine.unity]
project_subdir = "Game"
editor_version = "6000.3.23f1"
test_platform = "EditMode"
skipped_baseline = 20
golden_dir_env_vars = ["MS2_GOLDEN_DIR", "MS3_GOLDEN_DIR"]

[tests.fast]
runner = "dotnet"
project = "tests/Core.Tests/Core.Tests.csproj"

[tests.engine]
runner = "unity-testrunner"
ttl_seconds = 1800

[oracle]
control_must_pass = "AlwaysPasses_ControlGroup"
control_must_fail = "control_must_fail"

[limits]
max_impl_lines = 250

[static]
selftest_forbidden_probe = "UnityEngine.Debug.Log(0);"

[[static.forbidden]]
pattern = '\bUnityEngine\b'
label = "UnityEngine への依存"

[[static.forbidden]]
pattern = '\bMonoBehaviour\b'
label = "MonoBehaviour への依存"

[[static.forbidden]]
pattern = '\bTime\.(deltaTime|time|fixedDeltaTime)\b'
label = "グローバル時間への依存"

[[static.forbidden]]
pattern = '\bUnityEngine\.Random\b|\bnew Random\s*\(\s*\)'
label = "非決定な乱数"

[merge]
required_checks = ["test", "approval"]
```

## 4. 信頼規則（契約の取得）

### 4.1 取得手順

```
1. git -C <repo_dir> fetch origin <base_branch>          失敗 → 再試行（既存の _net と同じ規則）→ ABORT
2. contract_sha = git rev-parse origin/<base_branch>
3. text = git show <contract_sha>:.harness.toml         無い → ABORT（「契約がありません」）
4. tomllib.loads(text)                                   構文エラー → ABORT
5. スキーマ検証（§3・§7）                                 違反 → ABORT
6. 組み込みの禁止を足し、glob を正規化して、凍結したオブジェクトにする
```

- **作業ツリーの `.harness.toml` は読まない。** `git show <sha>:path` でオブジェクトから直接読むので、作業ツリーやサンドボックスが書き換わっていても影響しない
- `<base_branch>` は登録（`project.json`）から取る。契約の中には書かない（契約を取りに行く前に要るため）
- **1 回の実行で 1 回だけ読む。** 途中で main が進んでも読み直さない（試行ごとに規則が変わらないようにするため）
- 使った `contract_sha` を、実行記録（`runs.jsonl`）・TIMELINE・PR の承認依頼コメントに残す。「どの規則で判定したか」を後から復元できるようにする

### 4.2 読む場所ごとの扱い

| 読む場所 | 契約の出所 |
|:--|:--|
| scheduler（分解の出力の検査、PR 作成、マージ条件） | §4.1 |
| decompose（単位のホワイトリストの検査、分解役へのパスの受け渡し） | §4.1 |
| pipeline（すべての門、テストの実行） | §4.1。scheduler から `--contract-sha` を受け取り、同じ SHA を読む（食い違いを防ぐ） |
| 承認ゲート（GitHub Actions） | 使わない（`.github/ms4-approvers` を既定ブランチの最新から読む。既存のまま） |

## 5. 門の仕様

### 5.1 契約ファイルの変更禁止

| 場面 | 検出 | 扱い |
|:--|:--|:--|
| 実装役がサンドボックスで `.harness.toml` を変更・削除・改名した | `gate_whitelist`（組み込みの禁止に入っているので必ず当たる） | REJECT（RETRY）。実行記録に `contract_touched: true` を残す。サンドボックスは次の試行でリセットされ、契約はそもそもサンドボックスから読まないので、判定には影響しない |
| 本体の作業ツリーで `.harness.toml` が変わった | 既存の `gate_repo_untouched` | ABORT（サンドボックスからの脱走） |
| 分解役の単位の `whitelist` に `.harness.toml` などの禁止パスが入っている | decompose の `validate` と pipeline の `require_unit_safe` | decompose は rc=1（出力不良）、pipeline は ABORT |
| Issue のブランチ（`ms4/issue-N`）の差分に `.harness.toml` が含まれる | scheduler の PR 作成前の検査 | ABORT。契約の変更は、人間の承認つき PR 以外から入れさせない |

### 5.2 組み込みの禁止（契約から外せない）

```
.harness.toml
.github/**
.git/**
.gitattributes
.gitignore
<paths.test_dir>/**
<paths.units_dir>/**
<paths.audit_reports_dir>/**
<playtest.scene>                   （設定されているとき）
**/*Test.cs
**/*Tests.cs
**/golden*.json
**/*.unity
**/*.prefab
**/*.asset
**/*.asmdef
**/*.asmref
```

- `.unity` `.prefab` `.asset` は GUID 付きの YAML で、壊すと参照が静かに切れる（B7）
- `.asmdef` を変えられると、Pure C# から UnityEngine を使えないという「コンパイラによる強制」を外せる（A4）

### 5.3 実際に許すパス

```
norm(p)      = 区切りを / に、大文字小文字を畳む（Windows のファイルシステムは大文字小文字を区別しない）
unit_ok(p)   = norm(p) ∈ { norm(w) | w ∈ unit.whitelist }      ← 完全一致。単位の whitelist に glob は使わない
permit(p)    = ∃ g ∈ contract.paths.permitted       : glob_match(g, norm(p))
prohibit(p)  = ∃ g ∈ contract.paths.prohibited ∪ BUILTIN : glob_match(g, norm(p))

allowed(p)   = unit_ok(p) ∧ permit(p) ∧ ¬prohibit(p)
```

`.meta` の扱い（既存の GUID 保護を引き継ぐ）:
```
allowed(x.meta) = allowed(x) ∧（x が本体に存在しない新規ファイル）
既存の x.meta を変更した場合は、GUID が一致していても REJECT（既存の carry_out の ABORT 規則はそのまま）
```

判定する時点:
1. **decompose**: `validate` で、`whitelist` の各要素が `permit ∧ ¬prohibit` を満たすこと。満たさなければ rc=1（出力不良。分解役に差し戻す）
2. **pipeline 起動時**: `require_unit_safe` で同じ検査。満たさなければ ABORT（decompose を通らずに来た単位を止めるため）
3. **pipeline の各試行**: `gate_whitelist` で、サンドボックスの**すべての変更パス**（追加・変更・削除・改名の新旧両方）が `allowed` であること

glob の規則（`tomllib` と同じく標準ライブラリだけで実装する。3.12 には `PurePath.full_match` が無いので、正規表現へ変換する）:
- `*` は `/` を越えない、`**` は 0 個以上のディレクトリ、`?` は `/` 以外の 1 文字
- パターンも正規化する（`\` → `/`、大文字小文字を畳む）
- `..` と絶対パスは、パターンでも実際のパスでも ABORT

例（unity-2d の契約で）:

| パス | unit_ok | permit | prohibit | allowed |
|:--|:--|:--|:--|:--|
| `Game/Assets/Core/BossChargeState.cs` | ○ | ○ | × | **○** |
| `Game/Assets/Core/BossChargeState.cs.meta`（新規） | ○（x が ok） | — | × | **○** |
| `tests/Core.Tests/BossChargeStateTests.cs` | ○（分解役が誤って入れた） | × | ○ | **×**（decompose で差し戻し） |
| `Game/Assets/Core/Game.Core.asmdef` | ○ | × | ○ | **×** |
| `Game/Assets/Features/Boss/BossView.cs` | ×（単位に無い） | ○ | × | **×** |
| `.harness.toml` | — | × | ○ | **×** |

## 6. 移行表

分類: **契約** / **登録** / **共通** / **マシン** / **移管**（harness の外へ移す）/ **廃止**

### 6.1 `projects/unity-2d/project.json`

| キー | 分類 | 移行先 |
|:--|:--|:--|
| `id` | 登録 | そのまま |
| `repo_slug` | 登録 | そのまま |
| `base_branch` | 登録 | そのまま（契約を取りに行く前に要る） |
| `repo_dir` | マシン | `machine.toml` `[projects.unity-2d].repo_dir` |
| `out_dir` | マシン | `machine.toml` `[projects.unity-2d].out_dir` |
| `unity_project_subdir` | 契約 | `[engine.unity].project_subdir` |
| `test_dir` | 契約 | `paths.test_dir` |
| `impl_dir` | 契約 | `paths.impl_roots`（リストにする） |
| `fast_test_project` | 契約 | `tests.fast.project` |
| `units_dir` | 契約 | `paths.units_dir` |
| `required_checks` | 契約 | `merge.required_checks` |

### 6.2 `projects/unity-2d/pipeline.json`

| キー | 分類 | 移行先 |
|:--|:--|:--|
| `paths.out_dir` | マシン | `machine.toml` `[projects.unity-2d].pipeline_out_dir`（ゴールデンのステージング先を含む。今の `C:\src\.local\out\ms3` を維持する） |
| `paths.sandbox` | マシン | `machine.toml` `[projects.unity-2d].sandbox`（今の `ms3-sandbox` を維持する。Unity の Library の再生成に初回 11.9 分かかるため） |
| `paths.oracles_dir` | 移管 | harness のコードでは未使用。`ms3_capture_golden.ps1` だけが使う → unity-2d `tools/ms3_capture_golden.config.json` |
| `unity_exe` | マシン | `machine.toml` `[unity.editors]."6000.3.23f1"`（契約の `editor_version` で引く） |
| `unity_skip_baseline` | 契約 | `[engine.unity].skipped_baseline` |
| `golden_dir_env_vars` | 契約 | `[engine.unity].golden_dir_env_vars` |
| `control_groups.must_pass` / `must_fail` | 契約 | `oracle.control_must_pass` / `control_must_fail` |
| `oracle_filenames.holdout` / `disclosed` | 移管 | capture スクリプトだけが使う。harness のコードに参照は無い（pipeline は単位定義の `golden.holdout_rel` 等を使う。grep で確認） |
| `rel.capture_test` / `rel.disclosed_golden` | 移管 | 同上 |
| `golden_capture.*`（13 キー） | 移管 | 同上（MS3 のゴールデン採取の係数。harness のコードでは未使用） |
| `tautology.*`（7 キー） | 移管 | 同上 |
| `gates.max_retry` | 共通 | `config/pipeline.json`（全ゲーム共通の運用） |
| `implementer.*`（5 キー） | 共通 | `config/implementer.json`（agy の起動方法。ゲームに依らない） |
| `ttl_seconds.git` / `gh` / `implementer` | 共通 | `config/pipeline.json` |
| `ttl_seconds.dotnet_test` / `unity` | 契約（任意） | `tests.fast.ttl_seconds` / `tests.engine.ttl_seconds`。無ければ共通の既定 |

### 6.3 `config/scheduler.json`

| キー | 分類 | 移行先 |
|:--|:--|:--|
| `lock_path` | マシン | `machine.toml` `[paths].lock` |
| `branch_prefix` `labels` `required_clis` `commands` `audit.*` `ttl_seconds` `ci_*` `net_*` `comment_log_tail_chars` `max_issues_per_run` | 共通 | そのまま |

### 6.4 `config/decompose.json`

| キー | 分類 | 移行先 |
|:--|:--|:--|
| `prompt_file` | マシン | `machine.toml` `[paths].out_root` から導出（`<out_root>/<id>/decompose/prompt.md`） |
| `defaults.forbidden_leftover` / `forbidden_skip_attribute_regex` / `selftest_forbidden_probe` | 契約 | `[static]` |
| `defaults.max_impl_lines` | 契約 | `limits.max_impl_lines` |
| `defaults.forbidden_patterns` | 契約 | `[[static.forbidden]]`（UnityEngine の禁止は Unity 固有なので共通に置かない） |
| `cli` `headless_flag` `extra_flags` `prompt_arg_template` `ttl_seconds` `required_keys` `tautology_patterns` `prompt_template` | 共通 | そのまま |

### 6.5 `config/audit.json`

| キー | 分類 | 移行先 |
|:--|:--|:--|
| `out_dir` | 契約 | `paths.audit_reports_dir` |
| `model` `endpoint` `models_endpoint` `timeout_seconds` `system_prompt` | 共通 | そのまま |

### 6.6 単位定義（`tools/units/*.json`）

単位ごとの値（`whitelist` `prompt` `acceptance` `required_symbols` `human_check_point` `golden` など）は、単位に残す。

変更点:
- 今は decompose が既定値（`forbidden_patterns` など）を単位へ**写している**。移行後は写さない。実効値は **契約 ∪ 単位**（単位は足すことしかできない）
- `max_impl_lines` は min(契約, 単位)
- 新しいキー `playtest`: `"none"` / `"required"`（E の H2。既定 `"none"`）

### 6.7 移行の順序（Step 2 以降でコードを変えるとき）

1. harness に契約の読み込み（§4）と検証（§7）を足す。この時点では、**古い JSON との食い違いを検出して ABORT するだけ**（読み込み結果の比較モード）
2. unity-2d に `.harness.toml` を PR で入れる（人間の承認つき）
3. 比較モードで dry-run と自己検査を回し、差分 0 を確かめる
4. harness から古いキーを削除し、**両方にあったら ABORT** にする（既存の「重複を拒否」と同じ）
5. capture 用のキーを unity-2d に移管し、`ms3_capture_golden.ps1` の読み先を変える（PR）

## 7. 検証規則（読み込み時。すべて ABORT）

- `schema != 1`
- 知らないキー（全階層）
- 型の不一致（例: `max_impl_lines` が文字列）
- 必須キーの欠落
- パスの `..`・絶対パス・空文字
- `impl_roots` の要素が `permitted` のどれにも含まれない（実装の置き場なのに実装を許していない）
- `merge.required_checks` に `"approval"` が無い
- `oracle.quarantine` の要素に `approved_in` が無い
- `[[static.forbidden]]` があるのに `selftest_forbidden_probe` が無い、またはその probe がどの `pattern` にも当たらない（自己検査が空振りする。既存の教訓）
- `engine.kind = "unity"` なのに `[engine.unity]` が無い。`[engine]` の下に `kind` と一致しないエンジン名の表がある
- マシン設定に `editor_version` の実体が無い、またはそのファイルが存在しない

## 8. Step 2（実装）での検証方法

**テスト**
- 契約を git の**オブジェクト**から読むこと: 作業ツリーの `.harness.toml` を書き換えても結果が変わらない
- 契約が無い・構文エラー・知らないキー・型違い・`approval` が無い → それぞれ ABORT
- 組み込みの禁止が契約から外せないこと: `prohibited = []` でも `.harness.toml` と `tests/**` が禁止のまま
- §5.3 の例の表を、そのままテストケースにする（大文字小文字違い・`\` 区切り・`..` を足す）
- 同じ実行の中で main が進んでも、`contract_sha` が変わらないこと
- 単位の `forbidden` は足せるが、消せないこと

**変異テスト**（新規）
- サンドボックスから契約を読む
- 組み込みの禁止を外す
- `unit_ok` を省く（`permit ∧ ¬prohibit` だけにする）
- `permit` を省く
- 大文字小文字を畳まない
- 知らないキーを無視する
- `approval` の必須検査を外す
- `.meta` の新規限定を外す

**回帰**: 既存の 59 件と変異 32 件が、そのまま通ること
