"""総監督の無人の起動の関所（W-84、D-091）。

    python -m harness.director_launch [--config <hline.json>]

総監督を起こす前に、起動のときに読む文書（受信箱の drafts/director/INDEX.md ほか）の字数を測る。config/hline.json の
director.director_budget の上限を 1 つでも超えていれば、総監督を起こさず、超えた文書と字数を記録と report.md に出す。
上限の内なら、受信箱を作業ディレクトリにして 1 回だけ起こし、ターン数（--max-turns）と時間（ttl_seconds）の上限を掛ける。
トークンの上限は CLI に指定が無いので、走った後に利用量を照らし、超えていれば記録に「超過」と残す（次の起動は止めない）。
無い文書は字数 0 と数え、記録に「無い」と残す（誤りにしない）。記録は <out>/director/<時刻>.json に起動ごとに 1 つ。
総監督に渡す指示は docs/prompts/director_weekly.md（保護パス。操縦士の PR で入る）を走るときに読む。無ければ起こさない。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402
import hline_budget  # noqa: E402
import model_pin  # noqa: E402
import proc  # noqa: E402
from hline_base import CONFIG, ROOT, Infra, load_config, pinned_models, save_prompt, write_json  # noqa: E402

PROMPT = ROOT / "docs" / "prompts" / "director_weekly.md"
MISSING = "無い"
EXCESS = "超過"


def store_dir(cfg):
    return Path(cfg["out"]) / "director"


def measure(inbox, budget):
    """文書ごとの {path, chars, max, state}。無い文書は chars 0・state「無い」。"""
    rows = []
    for rel, limit in budget["files"].items():
        p = Path(inbox) / rel
        if p.is_file():
            rows.append({"path": rel, "chars": len(p.read_text(encoding="utf-8", errors="replace")), "max": limit, "state": "ある"})
        else:
            rows.append({"path": rel, "chars": 0, "max": limit, "state": MISSING})
    return rows


def exceeded(rows, budget):
    """上限を超えた文書と字数の文の列（文書ごとの上限・合計の上限）。"""
    found = [f"{r['path']} {r['chars']} 字（上限 {r['max']}）" for r in rows if r["chars"] > r["max"]]
    total = sum(r["chars"] for r in rows)
    if total > budget["total_max_chars"]:
        found.append(f"合計 {total} 字（上限 {budget['total_max_chars']}）")
    return found


def end_reason(code, out, cap):
    """終了の理由（正常・ターンの上限・TTL・CLI の誤り）。"""
    if code == 124:
        return "TTL"
    if hline_budget.turn_cutoff(out, cap):
        return "ターンの上限"
    if code != 0:
        return "CLI の誤り"
    return "正常"


def launch(cfg, now=None):
    """関所を通って、必要なら総監督を 1 回起こす。記録（dict）を返し、<out>/director/ に書く。"""
    agent = cfg["director"]
    budget = agent["director_budget"]
    model_pin.require(agent, "config/hline.json の director")
    model_pin.auxiliary_prefixes(agent, "config/hline.json の director")
    now = now or datetime.datetime.now()
    rows = measure(cfg["inbox"], budget)
    rec = {"at": now.isoformat(timespec="seconds"), "docs": rows, "total_chars": sum(r["chars"] for r in rows),
           "launched": False, "skipped_reason": None, "exceeded": exceeded(rows, budget), "end": None,
           "usage": None, "token_limit": budget["max_tokens"], "token_excess": False}
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    path = store_dir(cfg) / f"{stamp}.json"
    store_dir(cfg).mkdir(parents=True, exist_ok=True)
    prompt_file = Path(PROMPT)
    if rec["exceeded"]:
        rec["skipped_reason"] = "字数の上限を超えた文書があります: " + "、".join(rec["exceeded"])
    elif not prompt_file.is_file():
        rec["skipped_reason"] = "指示のファイルがありません"
    else:
        prompt = prompt_file.read_text(encoding="utf-8")
        cap = budget["max_turns"]
        args = hline_budget.capped_args(agent, proc.resolve_cli(agent["cli"]), cap)
        log = store_dir(cfg) / f"{stamp}.log"
        rec["launched"] = True
        write_json(path, rec)   # 起こした時刻は、CLI が返らなくても残す
        code, out, err = proc.run(args, cfg["inbox"], budget["ttl_seconds"], "総監督", input=prompt)
        log.write_text(out + "\n--- stderr ---\n" + err, encoding="utf-8")
        save_prompt(log, prompt)
        rec["end"] = end_reason(code, out, cap)
        if rec["end"] == "正常":
            try:
                pinned_models(agent, out, "総監督")
            except Infra as e:
                rec["end"], rec["error"] = "CLI の誤り", str(e)[:300]
        rec["usage"] = hline_budget.usage(out)
        used = (rec["usage"] or {}).get("total_tokens")
        rec["token_excess"] = isinstance(used, int) and not isinstance(used, bool) and used > budget["max_tokens"]
    write_json(path, rec)
    return rec


def line_of(rec):
    """report.md の「## H ライン」に出す 1 行。"""
    if not rec["launched"]:
        return f"- 総監督の起動: 起こさなかった（{rec['skipped_reason']}）"
    used = (rec.get("usage") or {}).get("total_tokens")
    line = f"- 総監督の起動: 起こした（{rec['at']}、終了: {rec['end']}、使ったトークン: {'不明' if used is None else used}）"
    if rec.get("token_excess"):
        line += f"／トークンの上限 {rec['token_limit']} を{EXCESS}"
    return line


def latest_line(cfg):
    """最新の記録の 1 行。記録が無い・読めないときは None（report.md の書き出しは止めない）。"""
    try:
        files = sorted(store_dir(cfg).glob("*.json"))
        return line_of(json.loads(files[-1].read_text(encoding="utf-8"))) if files else None
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness.director_launch")
    ap.add_argument("--config", default=str(CONFIG))
    cfg = load_config(ap.parse_args(argv).config)
    rec = launch(cfg)
    print(line_of(rec))
    return 0 if rec["launched"] and rec["end"] == "正常" else 1


if __name__ == "__main__":
    sys.exit(exitcode.normalized(main))
