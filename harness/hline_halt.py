"""H ライン（harness/hline.py）の停止の通知：poll が環境の異常か予期しない例外で終わったら、report.md の先頭に停止の節を書く。

同じ理由の停止が 2 回続いたら GitHub に issue を 1 件開く（題に理由を入れ、同じ題の開いた issue があれば開かない）。
設定の open_issue が false なら開かない（ベンチの走り）。題と本文の、ドライブからのパスとホームの下のパスは <path> に伏せる（公開のリポジトリのため）。
続いた回数は <out>/halt.json に置き、0 か 1 で終わった走行で消す。ここの失敗（report.md の書き込み・gh）は握りつぶし、poll の終了コードを変えない。
"""
import datetime
import json
import re
from pathlib import Path

import proc
from hline_base import ROOT

HEADING = "## 停止"
SECTION = re.compile(r"\A## 停止\n\n[^\n]*\n\n?")
MARK = "H ラインの停止: "
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|*?]*|/(?:home|Users)/[^\s\"'<>|*?/]+[^\s\"'<>|*?]*")


def reason_of(e):
    """Infra はその文、それ以外は例外の型と文の 1 行目。"""
    from hline_base import Infra
    if isinstance(e, Infra):
        return str(e).splitlines()[0] if str(e) else "環境の異常"
    first = (str(e).splitlines() or [""])[0]
    return f"{type(e).__name__}: {first}"


def _report(cfg):
    return Path(cfg["inbox"]) / "report.md"


def _write_section(cfg, reason, now):
    p = _report(cfg)
    text = SECTION.sub("", p.read_text(encoding="utf-8") if p.exists() else "")
    p.write_text(f"{HEADING}\n\n- {now:%Y-%m-%d %H:%M} {reason}\n\n" + text, encoding="utf-8")


def _remove_section(cfg):
    p = _report(cfg)
    if p.exists() and (text := p.read_text(encoding="utf-8")).startswith(HEADING):
        p.write_text(SECTION.sub("", text), encoding="utf-8")


def _count(cfg, reason):
    path = Path(cfg["out"]) / "halt.json"
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    n = last.get("count", 0) + 1 if last.get("reason") == reason else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"reason": reason, "count": n}, ensure_ascii=False), encoding="utf-8")
    return n


def masked(text):
    """ドライブからのパス（C:\\...・C:/...）とホームの下のパス（/home/<名>/...・/Users/<名>/...）を <path> に伏せたもの。"""
    return LOCAL_PATH.sub("<path>", text)


def _open_issue(cfg, reason):
    reason = masked(reason)
    title = MARK + reason
    ttl = cfg.get("ttl_seconds", {}).get("gh", 60)
    gh = proc.resolve_cli("gh")
    code, out, _ = proc.run(gh + ["issue", "list", "--state", "open", "--limit", "200", "--json", "title", "--jq", ".[].title"],
                            ROOT, ttl, "gh issue list")
    if code != 0 or title in out.splitlines():
        return
    proc.run(gh + ["issue", "create", "--title", title, "--body", f"poll が止まりました（同じ理由で 2 回以上続いています）。\n\n{reason}"],
             ROOT, ttl, "gh issue create")


def stopped(cfg, reason, now=None):
    """停止の節を書き、同じ理由が 2 回以上続いていれば issue を開く。例外は出さない。"""
    reason = reason.replace("\n", " ")
    try:
        _write_section(cfg, reason, now or datetime.datetime.now())
    except Exception as e:   # noqa: BLE001
        print(f"停止の節を書けません: {e}")
    try:
        if _count(cfg, reason) >= 2 and cfg.get("open_issue") is not False:
            _open_issue(cfg, reason)
    except Exception as e:   # noqa: BLE001
        print(f"停止の通知を出せません: {e}")


def recovered(cfg):
    """0 か 1 で終わった走行：停止の節と回数の記録を消す。例外は出さない。"""
    try:
        _remove_section(cfg)
    except Exception as e:   # noqa: BLE001
        print(f"停止の節を消せません: {e}")
    try:
        (Path(cfg["out"]) / "halt.json").unlink(missing_ok=True)
    except Exception as e:   # noqa: BLE001
        print(f"halt.json を消せません: {e}")


def settle(cfg, code, reason=None):
    """poll の終了コードに応じて、停止を記録するか消す。"""
    if code == 2:
        if reason is None:
            try:
                import hline_queue
                reason = (hline_queue.load_state(cfg).get("infra_halt") or {}).get("reason")
            except Exception:   # noqa: BLE001
                reason = None
        stopped(cfg, reason or "環境の異常（終了コード 2）")
    elif code in (0, 1):
        recovered(cfg)
