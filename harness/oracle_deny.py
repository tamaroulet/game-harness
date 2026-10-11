"""実装役（と、チャット型の走らせ役）に渡す禁止の列。受信箱・生ログ・非公開オラクルの下を読ませず、Bash を使わせない。
値は config/hline.json の inbox・out・oracles だけから作る（入口はこのモジュールだけ）。"""
import copy
import re

PLACES = ("inbox", "out", "oracles")
READERS = ("Read", "Grep", "Glob")


def rule_path(path):
    """Windows の絶対パス → 権限の規則のパス（C:\\src\\x → //c/src/x/**）。"""
    p = re.sub(r"[\\/]+", "/", str(path)).rstrip("/")
    if m := re.match(r"^([A-Za-z]):(/.*)?$", p):
        p = f"//{m.group(1).lower()}{m.group(2) or ''}"
    return p + "/**"


def deny_rules(cfg):
    missing = [k for k in PLACES if not isinstance(cfg.get(k), str) or not cfg[k].strip()]
    if missing:
        raise ValueError(f"config/hline.json に {', '.join(missing)} がありません（禁止の列を作れません）")
    return [f"{tool}({rule_path(cfg[k])})" for k in PLACES for tool in READERS] + ["Bash"]


def deny_list(cfg):
    """--disallowedTools に渡す 1 つの文字列。"""
    return ",".join(deny_rules(cfg))


def deny_flags(cfg):
    return ["--disallowedTools", deny_list(cfg)]


def with_deny(agent, cfg):
    """agent の extra_flags に禁止の列を足した写しを返す（設定は変えない）。"""
    out = copy.deepcopy(agent)
    out["extra_flags"] = list(out.get("extra_flags") or []) + deny_flags(cfg)
    return out
