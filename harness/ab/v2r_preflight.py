"""走行の前の検定（原則 P3。docs/design/v2r_instrument_redesign.md §4・§6 の 3）。実装役を呼ぶ前に、お金をかけずに測定器を検定する。

    python -m harness.ab.v2r_preflight [--manifest experiments/v2r/tasks.json] [--out-root C:/src/.local/out/ab]

**なぜ要るか**: v2r-dry-01・02 は、前提が作れない性質（測定器の欠陥）を、有料の走行で初めて見つけた。前提が作れるか、
生成物が凍結した版と同じか、実装役に渡す文面が閉じているかは、実装役を呼ばずに決まる。

**検定の項目**（どれか 1 つでも落ちれば不合格）：
1. マニフェストの凍結した入力（契約・性質の宣言・単位定義・型紙）が sha256 と一致する（common.load_manifest）
2. 性質の前提に実装の結果（after）を使う項が無い（propaudit、原則 P1）
3. 性質の宣言が、ゲームの base commit の構造化仕様・GDD で検査を通る。証拠の状態で前提が成り立つことを含む（propgen.validate）
4. 生成物（契約・参照データ・性質テスト）を作り直すと、マニフェストの generated_sha256 と一致する（測るものが凍結した版）
5. 描画器の語彙が全単射で、契約のメンバーを覆う（nlgen.check_vocabulary・check_contract）
6. タスクごとに、自然言語の仕様が描けて検査を通り（nlgen.check_rendering）、形式の prompt の語彙が閉じている（v2prep.vocabulary_problems）
7. 実装役のモデルが明示され、使ったモデルを記録できる出力形式になっている（model_pin.require_implementer）

**記録**：`<out-root>/preflight/<マニフェストの sha256>.json`。ドライバ（v2r のマニフェスト）は、同じマニフェストで合格した記録が
無ければ起動しない（`require`）。マニフェストを作り直したら、検定もやり直す。

**参考（判定に入れない）**：証拠の無い性質のうち、前提に開始状態だけでは決まらない項を持つものの数。無作為な系列で前提が
起きるかは実装を動かすまで分からない（成立回数は走行の metrics の property_hits で見る）。
"""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
if str(_HARNESS) not in sys.path:
    sys.path.insert(0, str(_HARNESS))

import exitcode  # noqa: E402
import model_pin  # noqa: E402
import nlgen  # noqa: E402
import project  # noqa: E402
import propaudit  # noqa: E402
import propgen  # noqa: E402
import unit_schema  # noqa: E402
from ab import common, v2prep, v2r  # noqa: E402

MANIFEST = common.ROOT / "experiments" / "v2r" / "tasks.json"
PROJECT = "falling-blocks"


def manifest_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stamp_path(manifest, out_root=common.OUT_ROOT):
    return Path(out_root) / "preflight" / f"{manifest_sha256(manifest)}.json"


def _reader(proj, base_commit):
    cfg = project.config("unit_schema")
    read = unit_schema.git_reader(proj["repo_dir"], base_commit, 120)
    return read(cfg["spec_path"]), read(cfg["gdd_path"])


def checks(manifest=MANIFEST, spec_gdd=None, proj=None):
    """[(項目, 問題の一覧)]。spec_gdd・proj を渡すとそれを使う（テスト用）。"""
    out = []
    try:
        m = common.load_manifest(manifest)
    except common.ABError as e:
        return [("マニフェストの凍結した入力", [str(e)])]
    out.append(("マニフェストの凍結した入力", []))
    if not common.is_v2r(m):
        return out + [("マニフェストの種類", [f"v2r のマニフェストではありません（kind={m.get('kind')!r}）"])]
    base = Path(m["_base"])
    decl = json.loads((base / m["properties"]).read_text(encoding="utf-8"))
    contract = json.loads((base / m["contract"]).read_text(encoding="utf-8"))
    rows = propaudit.audit(decl["properties"])
    out.append(("前提に after を使わない（P1）", [f"{r['id']}：outcome の項 {r['outcome']}" for r in rows if r["outcome"]]))
    proj = proj or project.load(PROJECT)
    try:
        spec, gdd = spec_gdd or _reader(proj, m["base_commit"])
    except Exception as e:  # noqa: BLE001 - 読めない理由をそのまま出す
        return out + [("構造化仕様・GDD の読み取り", [f"{type(e).__name__}: {e}"])]
    out.append(("性質の宣言の検査（証拠の状態の前提を含む）", propgen.validate(decl, spec, gdd, contract)))
    try:
        files = v2prep.generated(contract, decl, spec, gdd, proj, report_hits=True)
        want = m.get("generated_sha256") or {}
        got = {k: hashlib.sha256(v.encode("utf-8")).hexdigest() for k, v in files.items() if k in want}
        diff = sorted(k for k in want if got.get(k) != want[k])
        out.append(("生成物がマニフェストの版と同じ", [f"違う：{k}" for k in diff]))
    except (propgen.PropertyError, unit_schema.UnitSchemaError, common.ABError) as e:
        out.append(("生成物がマニフェストの版と同じ", [f"作れません：{e}"]))
    out.append(("描画器の語彙", nlgen.check_vocabulary() + nlgen.check_contract(contract)))
    spec_problems = []
    for t in m["tasks"]:
        unit = common.unit_of(m, t)
        mine, prior = v2r.split(decl["properties"], t["id"])
        body = nlgen.render_spec(mine, prior)
        spec_problems += [f"{t['id']}（自然言語）：{x}" for x in nlgen.check_rendering(mine + prior, body)]
        spec_problems += [f"{t['id']}（形式）：{x}" for x in v2prep.vocabulary_problems(unit["prompt"], mine + prior)]
    out.append(("実装役に渡す文面", spec_problems))
    try:
        model_pin.require_implementer(project.pipeline_config(proj)["implementer"],
                                      f"projects/{PROJECT}/pipeline.json の implementer")
        out.append(("実装役のモデルの固定", []))
    except model_pin.ModelPinError as e:
        out.append(("実装役のモデルの固定", [str(e)]))
    return out


def reference_only(manifest=MANIFEST):
    """参考：証拠の無い性質で、前提に開始状態だけでは決まらない項を持つものの ID。"""
    m = common.load_manifest(manifest)
    decl = json.loads((Path(m["_base"]) / m["properties"]).read_text(encoding="utf-8"))
    by = {p["id"]: p for p in decl["properties"]}
    return [r["id"] for r in propaudit.audit(decl["properties"]) if r["constructible"] and not by[r["id"]].get("witness")]


def run(manifest=MANIFEST, out_root=common.OUT_ROOT, spec_gdd=None, proj=None, now=None):
    """検定して記録を書く。記録（dict）を返す。"""
    res = checks(manifest, spec_gdd, proj)
    rec = {"manifest": str(Path(manifest).resolve()), "manifest_sha256": manifest_sha256(manifest),
           "at": (now or datetime.datetime.now)().isoformat(timespec="seconds"),
           "checks": [{"name": n, "problems": ps} for n, ps in res], "ok": all(not ps for _, ps in res)}
    path = stamp_path(manifest, out_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return rec


def require(manifest, out_root=common.OUT_ROOT):
    """ドライバの起動の前に呼ぶ。同じマニフェストで合格した記録が無ければ ABError。v2r でなければ何もしない。"""
    try:
        m = json.loads(Path(manifest).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if m.get("kind") != "v2r":
        return
    path = stamp_path(manifest, out_root)
    if not path.exists():
        raise common.ABError(f"走行の前の検定を、このマニフェストで通していません（{path.name}）。"
                             f"先に python -m harness.ab.v2r_preflight --manifest {manifest} を実行してください")
    rec = json.loads(path.read_text(encoding="utf-8"))
    if not rec.get("ok"):
        raise common.ABError(f"走行の前の検定に落ちています（{path}）。直して検定をやり直してください")


def render(rec, extra=()):
    lines = [f"# 走行の前の検定（{Path(rec['manifest']).name}、sha256 {rec['manifest_sha256'][:12]}…）", ""]
    for c in rec["checks"]:
        lines.append(f"- {'合格' if not c['problems'] else '不合格'}：{c['name']}")
        lines += [f"  - NG: {p}" for p in c["problems"][:10]]
    if extra:
        lines += ["", f"- 参考（判定に入れない）：証拠が無く、無作為な系列で前提が起きるのを待つ性質 {len(extra)} 件"]
    lines += ["", "合格" if rec["ok"] else "不合格"]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="走行の前の検定（原則 P3）")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out-root", default=str(common.OUT_ROOT))
    args = ap.parse_args(argv)
    rec = run(args.manifest, args.out_root)
    extra = reference_only(args.manifest) if rec["checks"] and not rec["checks"][0]["problems"] else []
    print(render(rec, extra))
    print(f"記録: {stamp_path(args.manifest, args.out_root)}")
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
