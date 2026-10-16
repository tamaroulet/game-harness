"""再実験 v2r の 4 条件（docs/design/v2r_protocol.md §1）。2 × 2 の要因計画：仕様の形 × 門の有無。

| 条件 | 仕様の形 | 門 |
|:--|:--|:--|
| A0 | 自然言語（harness/nlgen.py の決定論の描画） | なし（1 回だけ呼び、そのまま測る） |
| A1 | 自然言語 | あり（pipeline.judge の知らせで再試行、最大 9 呼び出し。性質の行に自然言語の文を添える） |
| B-G | 形式（性質の式と式の読み方。V2 と同じ単位定義の prompt） | なし |
| B | 形式 | あり |

**全条件ステートレス**：呼び出しごとに新しい会話で、同じ組み立て（§1.3）のプロンプトを渡す。条件で違うのは、仕様の節と、
門の知らせの有無だけ。作業場所・interface・埋め込み・作業場所の決まり・モデルは全条件で同じ。
"""
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import nlgen  # noqa: E402
from ab import common  # noqa: E402

# 条件 → (仕様の形, 門の有無)
CONDITIONS = {"A0": ("nl", False), "A1": ("nl", True), "B-G": ("formal", False), "B": ("formal", True)}
ORDER = common.V2R_CONDITIONS


def factors(condition):
    if condition not in CONDITIONS:
        raise common.ABError(f"v2r の条件は {list(ORDER)} のどれかです: {condition!r}")
    form, gate = CONDITIONS[condition]
    return {"form": form, "gate": gate}


def order_for(k):
    """繰り返し k（1 始まり）の条件の順。1 つずつずらして、時間帯の偏りを散らす（A0 A1 B-G B → A1 B-G B A0 → …）。"""
    s = (k - 1) % len(ORDER)
    return ORDER[s:] + ORDER[:s]


def _task_no(task_id):
    return int(task_id.lstrip("T"))


def properties(m):
    return json.loads((Path(m["_base"]) / m["properties"]).read_text(encoding="utf-8"))["properties"]


def split(props, task_id):
    """(このタスクの性質, 前のタスクの性質)。宣言の順。"""
    mine = [p for p in props if p["task"] == task_id]
    prior = [p for p in props if _task_no(p["task"]) < _task_no(task_id)]
    return mine, prior


def spec_text(m, task, unit, form):
    """仕様の節。形式は単位定義の prompt（V2 と同じ）。自然言語は、同じ題名と全タスク共通の節で、性質と読み方だけを
    nlgen の描画に替える（事実の集合は同じ。§2.2）。描画の検査に落ちたら ABError。"""
    if form == "formal":
        return unit["prompt"]
    from ab import v2prep  # v2prep は common を読むので、ここで読む
    mine, prior = split(properties(m), task["id"])
    body = nlgen.render_spec(mine, prior)
    problems = nlgen.check_rendering(mine + prior, body)
    if problems:
        raise common.ABError(f"{task['id']} の自然言語の仕様が検査を通りません: {problems[:5]}")
    return "\n\n".join([f"## タスク：{unit['title']}", body, v2prep.COMMON])


def sentences(m, task):
    """A1 の知らせに添える文 {性質の ID: 文}（このタスクと前のタスクの性質）。"""
    mine, prior = split(properties(m), task["id"])
    return nlgen.render_properties(mine + prior)


def assemble(workdir, spec, interface, embed, feedback, protocol):
    """プロンプトの組み立て（§1.3）。(プロンプト, 要素ごとの字数)。全条件で同じ手順。"""
    where = f"作業場所は {workdir} です。"
    sections = [("where", where), ("spec", spec), ("interface", interface), ("embed", embed)]
    if feedback:
        sections.append(("feedback", "前回の失敗:\n" + feedback))
    sections.append(("protocol", protocol))
    return "\n\n".join(text for _, text in sections), {k: len(text) for k, text in sections}
