"""エージェントの CLI（claude・agy）の呼び出しの引数と、利用枠切れ（429）の検出。

どの実行環境（Windows の上・箱の中）でも、どの役（調べ役・実装役）でも同じものを使う。
agy の stream-json の読みと agy の引数は harness/agy_stream.py、モデルの照合は harness/model_pin.py にある。
"""
import json
import re

HIT = re.compile(r"hit your (?:\w+ )?limit", re.I)
AGY_HIT = re.compile(r"RESOURCE_EXHAUSTED|quota exceeded|Rate limit exceeded|AI credits balance", re.I)
AGY_429 = re.compile(r"(?<![0-9A-Za-z])429(?![0-9A-Za-z])")


def args(agent, cli):
    """claude の形の引数（頼みは標準入力で渡す）：CLI・ヘッドレスの印・モデルの指定・ほかの印・出力の形。"""
    return (cli + [agent["headless_flag"], agent["model_flag"], agent["model"]]
            + agent["extra_flags"] + agent["output_format_args"])


def quota_message(out, err, agy=False):
    """出力が利用枠切れなら、その文（claude は JSON の result、無ければ標準エラーの行）。違えば None。終了コードは見ない。
    agy=True は agy の文言（agy_message）も見る。claude の判定は変えない。"""
    return _claude_message(out, err) or (agy_message(out, err) if agy else None)


def agy_message(out, err):
    """agy の出力が利用枠切れなら、その文。違えば None。
    RESOURCE_EXHAUSTED・quota exceeded・Rate limit exceeded は標準出力と標準エラーのどこでも当てる。
    429 は、標準エラーか、stream-json の result の error の中の、前後が数字でも英字でもない 429 だけ（応答の本文は見ない）。"""
    for text in (out, err):
        hit = next((line.strip()[:300] for line in (text or "").splitlines() if AGY_HIT.search(line)), None)
        if hit:
            return hit
    texts = [err or "", *_result_errors(out)]
    return next((line.strip()[:300] for t in texts for line in t.splitlines() if AGY_429.search(line)), None)


def _result_errors(out):
    """stream-json の result イベントの error（文字列）。"""
    for line in (out or "").splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        r = e.get("result") if isinstance(e, dict) and e.get("event") == "result" else None
        if isinstance(r, dict) and isinstance(r.get("error"), str):
            yield r["error"]


def _claude_message(out, err):
    try:
        doc = json.loads(out)
    except (ValueError, TypeError):
        doc = None
    if isinstance(doc, dict):
        text = str(doc.get("result") or "").strip()
        if doc.get("api_error_status") == 429 or HIT.search(text):
            return text or "api_error_status 429"
    return next((line.strip() for line in (err or "").splitlines() if HIT.search(line)), None)
