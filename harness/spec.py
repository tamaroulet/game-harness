"""構造化役。GDD から構造化仕様（docs/spec/spec.md・questions.md）を Claude CLI で作り、網羅検査器に通す。

    python harness/spec.py --project falling-blocks --gdd docs/gdd/source.md --out-dir docs/spec \\
        [--previous-spec <main の spec.md>] [--work-dir <試行の記録の置き場>] [--telemetry <JSON>]

終了コード: 0 = 検査に合格し、out-dir に書き出した（マージできるかは要約の mergeable）
            1 = 全試行で検査に不合格（構造化役の出力不良。何も書き出していない）
            2 = 環境異常（CLI が無い・異常終了・出力の封筒が読めない・モデルが違う・GDD や設定が不正）

規則の出所は docs/design/spec_pipeline.md §2・§4〜§8。

**作り直しの理由にするもの・しないもの**: 検査器の不合格（書式・根拠・網羅・参照・禁止語など、spec の不備）は、
理由と前回の出力を渡して作り直させる。「仮」・質問・錨の欠落は GDD の不足なので、作り直しても直らない。
合格のまま書き出し、スケジューラが ms4:questions を付けて止める（§7）。

**メタデータは LLM に書かせない**: project・gdd-version・gdd-sha256 はこのスクリプトが GDD から計算して付ける。
LLM が書いてしまった行は取り除く（sha256 の書き間違いで不合格になるのを避ける）。

**再現性**（§8）: 試行ごとにプロンプト・応答・検査結果をファイルに残し、sha256・モデル名・CLI のバージョン・
harness の SHA をテレメトリに記録する。同じ入力から同じ出力が作り直せることは保証しない（LLM のため）。
"""
import argparse
import hashlib
import json
import re
import string
import sys
import time
from datetime import datetime
from pathlib import Path

import gdd_check
import project
import telemetry
from proc import resolve_cli, run

SPEC_MARK = ("<<<SPEC_MD", "SPEC_MD>>>")
QUESTIONS_MARK = ("<<<QUESTIONS_MD", "QUESTIONS_MD>>>")
META_LINE_RE = re.compile(r"^\s*<!--\s*(project|gdd-version|gdd-sha256|version):.*-->\s*$")


class Env(Exception):
    """環境異常（rc=2）。"""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ============================================================ プロンプト

def number_lines(gdd_text):
    lines = gdd_text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(f"L{n}: {line}" for n, line in enumerate(lines, 1))


def build_prompt(cfg, gdd_text, terms, previous_spec=None, feedback=None):
    template = string.Template((project.ROOT / cfg["prompt_template_file"]).read_text(encoding="utf-8"))
    by_feature = {}
    for feature, term in terms:
        by_feature.setdefault(feature, []).append(term)
    term_lines = "\n".join(f"   - {f}: " + "、".join(ts) for f, ts in by_feature.items()) or "   - （なし）"
    prev = ""
    if previous_spec is not None:
        prev = ("## 前の版の spec.md（ID を引き継ぐ）\n\n"
                "内容が変わらない行は、前の版と同じ ID を使ってください。新しく作る行の ID の番号は、"
                "前の版にある同じ接頭辞の最大番号より大きくしてください（消えた番号を再利用しない）。\n\n"
                "```\n" + previous_spec.rstrip("\n") + "\n```\n")
    fb = ""
    if feedback is not None:
        problems, response = feedback
        fb = ("## 前回の出力は検査に不合格でした\n\n"
              "次の問題をすべて直した文書を、最初から全部作り直してください。\n\n"
              + "\n".join(f"- {p}" for p in problems)
              + "\n\n前回の出力:\n\n```\n" + response.rstrip("\n") + "\n```\n")
    return template.substitute(forbidden_terms=term_lines, previous_spec=prev, feedback=fb,
                               gdd_numbered=number_lines(gdd_text))


# ============================================================ 応答

def between(text, marks):
    start, end = marks
    i = text.find(start + "\n")
    j = text.find("\n" + end, i + 1) if i >= 0 else -1
    if i < 0 or j < 0:
        return None
    return text[i + len(start) + 1:j]


def with_meta(body, project_id, version, gdd_sha):
    """LLM が書いたメタデータの行を取り除き、GDD から計算した 3 行を先頭に付ける。"""
    lines = body.replace("\r\n", "\n").split("\n")
    while lines and (META_LINE_RE.match(lines[0]) or not lines[0].strip()):
        lines.pop(0)
    head = (f"<!-- project: {project_id} -->\n<!-- gdd-version: {version} -->\n"
            f"<!-- gdd-sha256: {gdd_sha} -->\n")
    return head + "\n".join(lines).rstrip("\n") + "\n"


# ============================================================ CLI

def cli_version(cfg):
    rc, out, err = run(resolve_cli(cfg["cli"]) + cfg["version_args"], project.ROOT,
                       cfg["ttl_seconds"]["version"], "claude --version")
    return (out or err).strip()[:80] if rc == 0 else None


def call_cli(cfg, prompt_path):
    """(本文, 利用量, 使われたモデル)。CLI の異常は Env。"""
    args = (resolve_cli(cfg["cli"]) + [cfg["headless_flag"], cfg["prompt_arg_template"].format(prompt_file=prompt_path),
                                       cfg["model_flag"], cfg["model"]]
            + cfg["extra_flags"] + cfg["output_format_args"])
    rc, out, err = run(args, project.ROOT, cfg["ttl_seconds"]["claude"], "claude (spec)")
    usage = telemetry.cli_usage(out, cfg["usage_format"])
    if rc != 0:
        raise Env(f"構造化役の CLI が異常終了しました (rc={rc}): {(err or out)[:300]}")
    text, why = telemetry.response_text(out, cfg["response_key"])
    if text is None:
        raise Env(f"構造化役の CLI の出力を読めません（{why}）: {out[:300]}")
    models = None
    try:
        doc = json.loads(out)
        if isinstance(doc.get("modelUsage"), dict):
            models = sorted(doc["modelUsage"])
    except (ValueError, AttributeError):
        pass
    if models is not None and not all(m.startswith(cfg["model"]) for m in models):
        raise Env(f"固定したモデル {cfg['model']} 以外が使われました: {models}（実験の条件がずれるので止めます）")
    return text, usage, models


# ============================================================ 本体

def structure(cfg, project_id, gdd_text, terms, previous_spec, work_dir, tel):
    """(rc, 検査の結果 or None, spec の本文, questions の本文)"""
    lines = gdd_text.split("\n")
    metas = [rx.fullmatch(lines[i]) if i < len(lines) else None for i, rx in enumerate(gdd_check.GDD_META)]
    if not all(metas):
        raise Env("GDD の先頭 2 行がメタデータ（project / version）ではありません")
    if metas[0].group(1) != project_id:
        raise Env(f"GDD の project（{metas[0].group(1)}）が --project（{project_id}）と違います")
    version, gdd_sha = int(metas[1].group(1)), sha256(gdd_text)
    tel.update(gdd={"version": version, "sha256": gdd_sha, "lines": len(lines) - (lines[-1] == "")},
               cli={"name": cfg["cli"], "version": cli_version(cfg), "model_requested": cfg["model"]})

    feedback = None
    for n in range(1, cfg["max_attempts"] + 1):
        d = Path(work_dir) / f"attempt_{n}"
        d.mkdir(parents=True, exist_ok=True)
        prompt = build_prompt(cfg, gdd_text, terms, previous_spec, feedback)
        (d / "prompt.md").write_text(prompt, encoding="utf-8")
        t0 = time.monotonic()
        text, usage, models = call_cli(cfg, d / "prompt.md")
        (d / "response.txt").write_text(text, encoding="utf-8")
        att = {"n": n, "prompt_sha256": sha256(prompt), "response_sha256": sha256(text),
               "seconds": round(time.monotonic() - t0, 1), "usage": usage, "models_used": models}
        tel["attempts"].append(att)

        spec_body, q_body = between(text, SPEC_MARK), between(text, QUESTIONS_MARK)
        if spec_body is None or q_body is None:
            problems = ["出力に区切り（<<<SPEC_MD … SPEC_MD>>> と <<<QUESTIONS_MD … QUESTIONS_MD>>>）が揃っていません"]
            result = None
        else:
            spec_text = with_meta(spec_body, project_id, version, gdd_sha)
            q_text = with_meta(q_body, project_id, version, gdd_sha)
            result = gdd_check.check(gdd_text, spec_text, q_text, terms, previous_spec)
            problems = result["problems"]
            (d / "check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        att.update(passed=not problems, problems_count=len(problems), problems=problems[:20])
        print(f"試行 {n}/{cfg['max_attempts']}: {'合格' if not problems else f'不合格 {len(problems)} 件'}")
        if not problems:
            return 0, result, spec_text, q_text
        feedback = (problems[:cfg["max_feedback_problems"]], text)
    return 1, None, None, None


def harness_sha():
    rc, out, _ = run(["git", "rev-parse", "HEAD"], project.ROOT, 60, "git rev-parse (harness)")
    return out.strip() if rc == 0 else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--gdd", required=True)
    ap.add_argument("--out-dir", required=True, help="合格したときだけ spec.md と questions.md を書く")
    ap.add_argument("--previous-spec", help="main にある前の版の spec.md（ID を引き継がせる）")
    ap.add_argument("--work-dir", help="試行ごとのプロンプト・応答・検査結果の置き場（既定は project.json の out_dir の下）")
    ap.add_argument("--telemetry", help="テレメトリの書き出し先（JSON）")
    a = ap.parse_args(argv)

    tel = {"schema": telemetry.SCHEMA, "tool": "spec", "project": a.project, "attempts": [],
           "started": datetime.now().isoformat(timespec="seconds")}
    rc = None
    try:
        p = project.load(a.project)
        cfg = project.config("spec")
        gdd_check.CFG = gdd_check.load_config()
        terms = gdd_check.load_terms(a.project)
        tel["harness_sha"] = harness_sha()
        gdd_text = Path(a.gdd).read_bytes().decode("utf-8")
        prev = Path(a.previous_spec).read_bytes().decode("utf-8") if a.previous_spec else None
        work = Path(a.work_dir) if a.work_dir else (
            Path(p["out_dir"]) / "spec" / datetime.now().strftime("%Y%m%d-%H%M%S"))
        tel["work_dir"] = str(work)
        rc, result, spec_text, q_text = structure(cfg, a.project, gdd_text, terms, prev, work, tel)
        if rc == 0:
            out = Path(a.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "spec.md").write_bytes(spec_text.encode("utf-8"))
            (out / "questions.md").write_bytes(q_text.encode("utf-8"))
            s = result["summary"]
            tel["result"] = {"passed": True, "mergeable": s["mergeable"], "spec_sha256": sha256(spec_text),
                             "questions_sha256": sha256(q_text), "provisional": len(s["provisional"]),
                             "questions": len(s["questions"]), "anchors_missing": s["anchors_missing"]}
            (work / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                               encoding="utf-8")
            print(f"書き出しました: {out / 'spec.md'}, {out / 'questions.md'}"
                  f"（{'マージ可' if s['mergeable'] else 'マージ不可: 仮・質問・錨の欠落あり'}）")
        else:
            tel["result"] = {"passed": False}
            print(f"NG: {cfg['max_attempts']} 回とも検査に不合格でした。何も書き出していません（記録: {work}）")
        return rc
    except Env as e:
        rc = 2
        print(f"ABORT: {e}")
        return rc
    except (gdd_check.ConfigError, project.ProjectError, OSError, UnicodeDecodeError, KeyError, ValueError) as e:
        rc = 2
        print(f"ABORT: {type(e).__name__}: {e}")
        return rc
    finally:
        telemetry.put(tel, "exit_code", rc, "sys.exit か例外で終了した")
        if a.telemetry:
            telemetry.write(a.telemetry, tel)


if __name__ == "__main__":
    import exitcode
    sys.exit(exitcode.normalized(main))
