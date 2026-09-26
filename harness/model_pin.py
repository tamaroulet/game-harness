"""エージェントのモデルの固定と記録（Phase 4 の前の是正、2026-09-26 のオーナーの通達）。

    python -m harness.model_pin            すべてのエージェント設定がモデルを明示しているかを検査する

**なぜ要るか**: v2 の報告書（experiments/v2/results/run/report.md）で、仕様分解役（declare・decompose）のモデル名が
「記録なし（CLI の既定）」になった。config/decompose.json に --model が無く、CLI の既定のモデルで動いていたので、
事前投資（1.202043 USD）をどのモデルが書いたのか、一次情報のどこにも残っていなかった。

**鉄則**:
1. ハーネスが呼ぶすべてのエージェント（分解役・構造化役・実装役）の設定に、`model_flag` と正確なモデルの ID を書く。
   CLI の既定のモデルには頼らない。書いていなければ、起動の時点で止める（`require`）
2. 実行のたびに、CLI が報告したモデル（claude は JSON の modelUsage、agy は stream-json の model）を記録し、
   設定のモデルと照合する。報告が無い・違うときは止める（`check_claude`・`check_reported`）

既定値はコードに持たない。持つと、設定を消したときに黙って既定に戻り、どのモデルで動いたかが設定から読めなくなる。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exitcode  # noqa: E402  python -m harness.model_pin でも python harness/model_pin.py でも読めるように

ROOT = Path(__file__).resolve().parent.parent


class ModelPinError(Exception):
    """モデルが設定に明示されていない、または報告されたモデルが設定と違う（環境の異常として扱う）。"""


def require(cfg, where, key="model"):
    """設定が `model_flag` と正確なモデルの ID（`key`）を持つこと。モデルの ID を返す。"""
    flag, model = cfg.get("model_flag"), cfg.get(key)
    if not isinstance(flag, str) or not flag.strip():
        raise ModelPinError(f"{where} に model_flag がありません（CLI の既定のモデルには頼りません。例: \"--model\"）")
    if not isinstance(model, str) or not model.strip():
        raise ModelPinError(f"{where} に {key}（正確なモデルの ID）がありません（CLI の既定のモデルには頼りません）")
    return model


def auxiliary_prefixes(cfg, where):
    """CLI の内部処理用として許すモデルの接頭辞。設定が無い・形が違う・固定したモデルに当たるなら止める。"""
    aux = cfg.get("auxiliary_models")
    if (not isinstance(aux, list) or not aux
            or not all(isinstance(x, str) and x.strip() for x in aux)):
        raise ModelPinError(f"{where} の auxiliary_models が、空でない文字列の配列ではありません"
                            "（CLI の内部処理用として許すモデルの接頭辞。例: [\"claude-haiku-\"]）")
    bad = [x for x in aux if cfg["model"].startswith(x)]
    if bad:
        raise ModelPinError(f"auxiliary_models の {bad} が、固定したモデル {cfg['model']} に当たります"
                            "（固定したモデル自身を内部処理用に数えると、使われたかどうかを確かめられません）")
    return tuple(aux)


def claude_models(out):
    """claude CLI の JSON にある modelUsage。({モデル: 利用量}, None) か (None, 理由)。"""
    try:
        doc = json.loads(out)
    except ValueError as e:
        return None, f"CLI の出力を JSON として読めません: {e}"
    if not isinstance(doc, dict):
        return None, "CLI の出力が表ではありません"
    got = doc.get("modelUsage")
    if not isinstance(got, dict):
        return None, "CLI の出力に modelUsage がありません"
    return got, None


def check_claude(cfg, used, why, where):
    """固定したモデルが実際に使われ、ほかは CLI の内部処理用だけであること。使われたモデルの列（決定論の順）を返す。

    CLI は要求したモデルのほかに、内部処理で小型のモデルを呼ぶ（実測：claude 2.1.258 の -p が claude-opus-5 の要求に
    claude-haiku-4-5 を併用した）。それは auxiliary_models の接頭辞で許す。**modelUsage が無いときは止める**
    （どのモデルで動いたかを記録できない実行は受け付けない）。
    """
    aux = auxiliary_prefixes(cfg, where)
    if used is None:
        raise ModelPinError(f"{where}：使われたモデルを記録できません（{why}）")
    models = sorted(used)
    if not any(m.startswith(cfg["model"]) for m in models):
        raise ModelPinError(f"{where}：固定したモデル {cfg['model']} が使われていません: {models}")
    stray = [m for m in models if not m.startswith(cfg["model"]) and not m.startswith(aux)]
    if stray:
        raise ModelPinError(f"{where}：固定したモデル {cfg['model']} と、CLI の内部処理用（{', '.join(aux)}）以外が"
                            f"使われました: {stray}")
    return models


def check_reported(requested, reported, where):
    """CLI が報告したモデル（agy の stream-json の model）が、設定のモデルと一字一句同じであること。"""
    if not isinstance(reported, str) or not reported:
        raise ModelPinError(f"{where}：CLI が使ったモデルを報告していません（記録できない実行は受け付けません）")
    if reported != requested:
        raise ModelPinError(f"{where}：設定のモデル {requested} と、CLI が報告したモデル {reported} が違います")
    return reported


def require_implementer(imp, where):
    """実装役（agy）の設定：モデルの明示（model_name）と、使ったモデルを報告する出力形式（stream-json）。

    agy の `--output-format json` の出力にはモデルが無い（tests/test_telemetry.py の AGY_JSON が実測の形）。
    stream-json は init にモデルを載せる。記録できない形式では起動させない。
    """
    model = require(imp, where, "model_name")
    if imp.get("cli") == "agy" and "stream-json" not in (imp.get("output_format_args") or []):
        raise ModelPinError(f"{where} の output_format_args が stream-json ではありません"
                            "（agy は stream-json の init にしか使ったモデルを載せないので、記録できません）")
    return model


# ============================================================ 設定の一斉検査

def agent_configs(root=ROOT):
    """ハーネスが呼ぶエージェントの設定。[(場所, 設定の表, モデルのキー)]。"""
    out = []
    for name in ("decompose", "spec"):
        p = Path(root) / "config" / f"{name}.json"
        if p.exists():
            out.append((f"config/{name}.json", json.loads(p.read_text(encoding="utf-8")), "model"))
    for p in sorted((Path(root) / "projects").glob("*/pipeline.json")):
        cfg = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(cfg.get("implementer"), dict):
            out.append((f"{p.relative_to(root).as_posix()} の implementer", cfg["implementer"], "model_name"))
    return out


def problems(root=ROOT):
    found = []
    for where, cfg, key in agent_configs(root):
        try:
            if key == "model_name":
                require_implementer(cfg, where)
            else:
                require(cfg, where, key)
            if cfg.get("cli") == "claude":
                auxiliary_prefixes(cfg, where)
        except ModelPinError as e:
            found.append(str(e))
    return found


def main(argv=None):
    found = problems()
    for where, cfg, key in agent_configs():
        print(f"{where}: {cfg.get('cli')} {cfg.get('model_flag')} {cfg.get(key)}")
    for f in found:
        print(f"NG: {f}")
    print("合格" if not found else f"不合格（{len(found)} 件）")
    return 0 if not found else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
