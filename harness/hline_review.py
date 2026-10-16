"""H ライン（harness/hline.py）の取り込みの段の監修役（LLM）：機械の監修を通った What を、台帳のタスクと大局に照らして 1 回だけ批判する。

監修役は合格を決めない。`revise` なら What を受信箱に残し、`pass` なら今までどおり取り込む。Gate 1（hline.gate）には入れない。
同じ（What の全文・タスクの欄・指示）の組み合わせには 2 回目から呼ばず、生ログの置き場（cfg["out"]）の控えの判定を使う。
呼び出しが起動の失敗・時間切れ・利用枠切れで終わったときは、判定を控えない（次の走行で呼び直す）。
"""
import hashlib
import json
from pathlib import Path

import hline_base
import hline_quota
import infra_retry
import proc
from hline_base import Infra, ROOT

FIELDS = ("title", "purpose", "serves", "acceptance", "oracle", "verification")
KINDS = ("goal_mismatch", "acceptance_mismatch", "unverifiable", "internal_prescription", "oversize", "ambiguity")


class Unavailable(Exception):
    """判定を得られなかった（起動の失敗・時間切れ・利用枠切れ・モデルの照合の失敗）。控えず、受信箱に残す。"""


def store_dir(cfg):
    return Path(cfg["out"]) / "review"


def instruction(cfg):
    """監修役への指示の全文。base（cfg["base"]）の cfg["reviewer"]["instruction"] から読む。読めなければ Unavailable。"""
    rel = cfg["reviewer"]["instruction"]
    code, out, err = proc.run(["git", "show", f"{cfg['base']}:{rel}"], ROOT, cfg["ttl_seconds"]["git"], "git show (reviewer instruction)")
    if code != 0:
        raise Unavailable(f"{cfg['base']} の {rel} を読めません: {' '.join((err or out or '').split())[:200]}")
    return out


def fields_of(row):
    return {k: row.get(k) for k in FIELDS}


def rung_peers(ledger, task):
    """そのタスクの serves と同じ rung を持つ台帳のタスクの title と acceptance。"""
    serves = (ledger.get(task, {}).get("row") or {}).get("serves")
    if not serves:
        return []
    return [{"id": i, "title": (v.get("row") or {}).get("title"), "acceptance": (v.get("row") or {}).get("acceptance")}
            for i, v in ledger.items() if (v.get("row") or {}).get("rung") == serves]


def build_input(instr, what, task, row, peers):
    """監修役への入力：指示の全文、`---` の行、(1) What の全文、(2) タスクの欄、(3) 同じ rung のタスク。"""
    dump = lambda o: json.dumps(o, ensure_ascii=False, indent=1, default=str)   # noqa: E731
    return "\n".join([instr.rstrip("\n"), "---", "(1) What の全文", what.rstrip("\n"), "",
                      f"(2) 台帳のタスクの欄（{task}）", dump(fields_of(row)), "",
                      f"(3) 寄与する横木（{row.get('serves')}）のタスクの title と acceptance", dump(peers)]) + "\n"


def key_of(instr, what, row):
    blob = json.dumps({"instruction": instr, "what": what, "fields": fields_of(row)}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse(out):
    """CLI の出力から監修役の JSON を読む。({"verdict", "findings"}, None) か (None, 理由)。"""
    try:
        doc = json.loads(out)
        text = doc.get("result") if isinstance(doc, dict) and "result" in doc else out
        if isinstance(text, str):
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            text = json.loads(text)
    except (ValueError, AttributeError) as e:
        return None, f"JSON として読めません（{type(e).__name__}）"
    if not isinstance(text, dict) or text.get("verdict") not in ("pass", "revise"):
        return None, "verdict が pass か revise ではありません"
    found = text.get("findings", [])
    if not isinstance(found, list) or not all(isinstance(f, dict) and isinstance(f.get("kind"), str)
                                                and isinstance(f.get("text"), str) for f in found):
        return None, "findings が kind と text を持つ列ではありません"
    return {"verdict": text["verdict"], "findings": [{"kind": f["kind"], "text": f["text"]} for f in found]}, None


def call(cfg, prompt, log):
    """監修役を 1 回呼ぶ（道具は許さない）。CLI の標準出力を返す。失敗は Unavailable。"""
    agent = cfg["reviewer"]
    args = hline_base.implementer_args(agent, proc.resolve_cli(agent["cli"]))
    code, out, err = proc.run(args, ROOT, cfg["ttl_seconds"]["reviewer"], "監修役", input=prompt)
    Path(log).write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
    hline_base.save_prompt(log, prompt)
    if hit := hline_quota.message(out, err):
        raise Unavailable(f"監修役の利用枠が尽きました: {hit}")
    if code in infra_retry.INFRA_EXIT_CODES:
        raise Unavailable(f"監修役の CLI が終了コード {code}: {infra_retry.classify_exit(code, err)}")
    try:
        hline_base.pinned_models(agent, out, "監修役")
    except Infra as e:
        raise Unavailable(str(e)) from e
    return out


def decide(cfg, instr, what, task, row, peers):
    """判定（控えがあればそれ）。控えは読めなかった場合も含む。呼び出しの失敗は控えず Unavailable。"""
    key = key_of(instr, what, row)
    path = store_dir(cfg) / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    store_dir(cfg).mkdir(parents=True, exist_ok=True)
    out = call(cfg, build_input(instr, what, task, row, peers), store_dir(cfg) / f"{key}.log")
    doc, why = parse(out)
    result = doc or {"verdict": "unreadable", "reason": why, "head": out[:300]}
    hline_base.write_json(path, result)
    return result


def reason_of(result):
    if result["verdict"] == "unreadable":
        return f"監修役の出力を読めませんでした（{result['reason']}）。出力の先頭: {result['head']}"
    items = "; ".join(f"[{f['kind']}] {f['text']}" for f in result["findings"]) or "（findings なし）"
    return f"監修役が差し戻しました: {items}"


def review(cfg, ledger, task, what):
    """取り込んでよければ None。差し戻す・判定を得られないときはその理由。"""
    try:
        instr = instruction(cfg)
        row = (ledger.get(task, {}).get("row")) or {}
        result = decide(cfg, instr, what, task, row, rung_peers(ledger, task))
    except Unavailable as e:
        return f"監修役の判定を得られませんでした（判定は控えず、次の走行で呼び直します）: {e}"
    return None if result["verdict"] == "pass" else reason_of(result)
