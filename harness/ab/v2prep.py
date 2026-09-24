"""v2 の A/B 実験の入力を作って凍結する（V2-5）。LLM を使わない（費用 0）。

    python -m harness.ab.v2prep

docs/design/v2_contract_foundry.md §5.3・§11。V2-4 の宣言（experiments/v2/contract.json・properties.json）と
要求文（experiments/b4_ab/requirements/）から、次を機械的に作る。

- `experiments/v2/units/T1〜T5.json`：task_kind が property の単位定義。受入は、そのタスクの性質テストの公開シード版
- `experiments/v2/templates/`：条件 A の固定テンプレート（v1 と同じ文面を写す）
- `experiments/v2/tasks.json`：マニフェスト。入力の sha256 と、生成物（contractgen・propgen）の sha256 で凍結する

**base にある型**：base（falling-blocks#12・#17 の後）には GamePhase・MinoType・Rotation・ActiveMino が既にある。
契約はこれらを生成しない（同じ名前の型が 2 つになる）。生成するのは TickInput・Cell・IGameState と、探針・性質テスト。
**契約の置き場**：Core の直下（v1 の実装ファイルと同じ）。テストプロジェクトが Core の下位フォルダまで拾うかを、
総監督は Core とテストを見ないので確かめられないため（設計 §3.1 の Contracts/ からの変更）。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import contractgen  # noqa: E402
import exitcode  # noqa: E402
import implementer_context  # noqa: E402
import project  # noqa: E402
import propgen  # noqa: E402
import unit_schema  # noqa: E402
from ab import common  # noqa: E402

V2 = common.ROOT / "experiments" / "v2"
V1 = common.ROOT / "experiments" / "b4_ab"
PROJECT = "falling-blocks"
EXISTING = ["GamePhase", "MinoType", "Rotation", "ActiveMino"]
# 実装役が書くのは振る舞いの型だけ（データ型は契約の生成物。v2 §3.1）。base にある振る舞いの型は GameState と
# Board で、Board はすでに IsOccupied・InBounds を持つ（falling-blocks の tools/units/issue_12.json）。
# 盤面の占有を Board に置くのは既存の設計に沿った判断なので、Board.cs も書き換えてよい（v2.1 §1.3 の 1、
# 2026-09-24 の裁定。v2-dry-02 の B は Board.cs の編集で試行 1 を失った）。
# gate_static_common は whitelist のファイルがすべて在ることを要求するので、base に無いファイル（v1 の
# MinoShape.cs）は入れない（v2-dry-01 の B で 2 回とも「MinoShape.cs が存在しません」）
WHITELIST = ["GameState.cs", "Board.cs"]
MEASURE_HIDDEN_SEEDS = 20

COMMON = """## 全タスク共通（v2）

- IGameState・TickInput・Cell・GddReference は契約の生成物で、書き換えない（whitelist の外にある）。GameState は IGameState を実装する
- 参照データ（PR-xx）は、GddReference の定数（PR-03 なら GddReference.PR_03）と、形の表 GddReference.Shape、回転補正候補の表 GddReference.Kicks を使う。値を書き写さない
- 既存の GamePhase・MinoType・Rotation・ActiveMino はそのまま使う。既存の公開メンバーの名前・型・引数を変えない。消さない
- 受入は、上の性質（前提が成り立つティックでは、帰結が必ず成り立つ）を、ランダムな開始状態と入力の列で確かめる性質テスト。前のタスクの性質も守り続ける
- 保存則（RL-36）が常に成り立つこと
- 差分は追加 250 行以下かつ削除 100 行以下"""


# 条件 A の固定テンプレート（v2.1c）。v1 の文面から、受入テストの置き場と「読まない・書き換えない」の頼みを外した。
# 実装役は細い作業場所（書き換えてよいファイルと契約だけ）で動き、テストはそこに無い（v2.1c §3 の 3、裁定 2）
TEMPLATES = {
    "initial": """あなたは落ちものパズル falling-blocks の実装者です。次のタスクを実装してください。

作業場所は {workdir} です。ここには書き換えてよいファイルと契約だけがあり、中身は下に埋め込んであります。
計画・実装案・確認を返さず、いま直接ファイルを作成・編集してください。

# タスク {task_id}：{title}

{prompt}

## 作る型とメンバー

{interface}

## 書き換えてよいファイル

{whitelist}

## 受入

受入は、上の性質を確かめる性質テストです。外側で実行し、破れたら反例を伝えます。
実装が終わったら、変更したファイルの一覧だけを答えてください。
""",
    "retry": """タスク {task_id} の受入テストが通りませんでした（{attempt} 回目 / 最大 {max_attempts} 回）。

## 落ちたテスト

{failed_tests}

## 失敗の出力（末尾 {tail_lines} 行）

{failure_tail}

実装を直してください。直したら、変更したファイルの一覧だけを答えてください。
""",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def requirement(task):
    """要求文から見出しを除き、バッククォートを外す（単位定義の prompt はバッククォートの中身を検査する）。"""
    text = (V1 / "requirements" / f"{task}.md").read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    title = re.sub(r"^#\s*T\d+：", "", lines[0]).strip()
    return title, "\n".join(lines[1:]).replace("`", "").strip()


def _task_no(task):
    return int(task.lstrip("T"))


def _prop_line(p):
    return f"- {p['id']}（{p['rule']}）：前提 {p['given']} のとき、{p['then']}"


def unit_for(task, contract, props, impl_dir, template):
    title, body = requirement(task)
    mine = [p for p in props["properties"] if p["task"] == task]
    parts = [body, "## このタスクの性質（受入）", "\n".join(_prop_line(p) for p in mine)]
    # 前のタスクの性質も、性質の宣言から機械的に渡す（v2.1c §3 の 2）。累積で守らせるものを実装役にも見せる。
    # 仕様書の文章の抜き出し（v2.1b）はやめた。自然言語の prompt から ID を拾う正規表現は、範囲の表記で欠けた
    prior = [p for p in props["properties"] if _task_no(p["task"]) < _task_no(task)]
    if prior:
        parts += ["## 前のタスクの性質（守り続ける）", "\n".join(_prop_line(p) for p in prior)]
    prompt = "\n\n".join(parts + [COMMON])
    unit = {k: template[k] for k in template if k not in ("id", "title", "prompt", "interface", "whitelist",
                                                          "impl_files", "acceptance", "task_kind")}
    wl = [f"{impl_dir}/{f}" for f in WHITELIST]
    unit.update(id=f"v2_{task.lower()}", title=title, task_kind="property", prompt=prompt, interface=contract,
                whitelist=wl, impl_files=wl,
                acceptance={"cases": [], "required_tests": [f"Properties{task}Cases.{p['id'].replace('-', '_')}_Public"
                                                             for p in mine]})
    return unit


def reference(contract, props, spec_text, proj):
    """参照データの契約（GddReference.cs）。({相対パス: 本文}, 定数にした PR の ID の列)（v2.1c §3 の 1）。"""
    enums = {t["name"]: t["values"] for t in contract["types"] if t["kind"] == "enum"}
    return contractgen.reference_file(propgen.param_rows(spec_text), enums, proj["impl_dir"], subdir="")


def generated(contract, props, spec_text, gdd_text, proj, tasks=None):
    """置く生成物 {リポジトリからの相対パス: 本文}。tasks で性質テストのクラスを絞る。"""
    files = contractgen.generate(contract, proj["impl_dir"], proj["test_dir"], existing=EXISTING, subdir="")
    files.update(reference(contract, props, spec_text, proj)[0])
    files.update(propgen.generate(props, spec_text, gdd_text, contract, proj["test_dir"], tasks=tasks))
    return files


def prepare():
    proj = project.load(PROJECT)
    imp = project.pipeline_config(proj)["implementer"]
    cfg = project.config("unit_schema")
    v1m = json.loads((V1 / "tasks.json").read_text(encoding="utf-8"))
    read = unit_schema.git_reader(proj["repo_dir"], v1m["base_commit"], 120)
    spec_text, gdd_text = read(cfg["spec_path"]), read(cfg["gdd_path"])
    contract = json.loads((V2 / "contract.json").read_text(encoding="utf-8"))
    props = json.loads((V2 / "properties.json").read_text(encoding="utf-8"))

    (V2 / "units").mkdir(exist_ok=True)
    (V2 / "templates").mkdir(exist_ok=True)
    tasks = []
    ref_ids = set(reference(contract, props, spec_text, proj)[1])
    for t in v1m["tasks"]:
        template = json.loads((V1 / t["unit"]).read_text(encoding="utf-8"))
        unit = unit_for(t["id"], contract, props, proj["impl_dir"], template)
        kind, problems = unit_schema.check(json.dumps(unit, ensure_ascii=False).encode("utf-8"), read)
        if problems:
            raise common.ABError(f"{t['id']} の単位定義がスキーマ門を通りません: {problems[:5]}")
        # 単位定義が参照する参照データは、すべて契約（GddReference）に定数としてあること（範囲の表記も展開して見る）
        missing = sorted(implementer_context.referenced_params(unit["prompt"]) - ref_ids)
        if missing:
            raise common.ABError(f"{t['id']} が参照する参照データが契約にありません: {missing}")
        path = V2 / "units" / f"{t['id']}.json"
        path.write_text(json.dumps(unit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        tasks.append({"id": t["id"], "title": unit["title"], "unit": f"units/{t['id']}.json", "unit_sha256": sha(path),
                      "superseded_tests": []})
    for k, text in TEMPLATES.items():
        (V2 / "templates" / f"{k}.md").write_text(text, encoding="utf-8", newline="\n")

    files = generated(contract, props, spec_text, gdd_text, proj)
    manifest = {
        "schema": 1, "experiment": "v2", "kind": "v2",
        "_comment": "v2 の A/B 実験の入力（docs/design/v2_contract_foundry.md §5.3）。harness/ab/v2prep.py が作る。手で編集しない",
        # 実装役のモデルは、走らせる設定（pipeline.json）から写す（v2.1c で思考の重さをモデル名で選ぶようにした）
        "base_commit": v1m["base_commit"], "gdd_sha256": v1m["gdd_sha256"],
        "implementer": {"cli": imp["cli"], "model": imp["model_name"]},
        "max_attempts": v1m["max_attempts"], "invariant_seeds": v1m["invariant_seeds"],
        "contract": "contract.json", "contract_sha256": sha(V2 / "contract.json"),
        "properties": "properties.json", "properties_sha256": sha(V2 / "properties.json"),
        "existing_types": EXISTING, "measure_hidden_seeds": MEASURE_HIDDEN_SEEDS,
        # 契約にした参照データの ID と、実装役に埋め込む契約（impl_dir の下の生成物）の字数（v2.1c §3 の 4 の静的な検査）
        "reference_ids": sorted(ref_ids, key=lambda x: int(x.split("-")[1])),
        "contract_chars": sum(len(text) for rel, text in files.items() if rel.startswith(proj["impl_dir"] + "/")),
        "generated_sha256": {rel: hashlib.sha256(text.encode("utf-8")).hexdigest() for rel, text in sorted(files.items())},
        "templates": {"initial": "templates/initial.md", "initial_sha256": sha(V2 / "templates" / "initial.md"),
                      "retry": "templates/retry.md", "retry_sha256": sha(V2 / "templates" / "retry.md"),
                      "retry_tail_lines": v1m["templates"]["retry_tail_lines"]},
        "test_dir": f"{proj['test_dir']}/{propgen.OUT_DIR}",
        "tasks": tasks,
    }
    (V2 / "tasks.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
                                   newline="\n")
    return manifest


def main(argv=None):
    try:
        m = prepare()
    except common.ABError as e:
        print(f"ABORT: {e}")
        return exitcode.ABORT
    print(f"書き出し: {V2 / 'tasks.json'}（タスク {len(m['tasks'])}、生成物 {len(m['generated_sha256'])}）")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
