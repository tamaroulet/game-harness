"""プロジェクト（ゲーム 1 本）の設定を読む。harness の道具はすべてここを通す。

    projects/<id>/project.json    リポジトリ・ローカルパス・置き場所（このゲーム固有）
    projects/<id>/pipeline.json   実装パイプラインの設定（旧 ms3.config.json）
    config/<name>.json            全プロジェクト共通の設定

**なぜ分けたか**

道具（スケジューラ・パイプライン・分解役・監査）が unity-2d の中にあった。
2 本目のゲームを作るたびに道具を複製すると、門の修正が片方にしか入らない
（散文の教訓が再発を防がなかったのと同じ型の事故になる）。道具は 1 本にし、
ゲームごとの違いは設定だけに閉じる。

リポジトリのパスは project.json だけに書く。pipeline.json 等に再掲しない。
"""
import json
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
ROOT = HARNESS_DIR.parent

REQUIRED_KEYS = ["id", "repo_slug", "repo_dir", "base_branch", "unity_project_subdir",
                 "test_dir", "impl_dir", "fast_test_project", "units_dir", "out_dir"]


class ProjectError(Exception):
    pass


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ProjectError(f"設定がありません: {path}")
    except ValueError as e:
        raise ProjectError(f"JSON として読めません: {path}: {e}")


def config(name):
    return _read(ROOT / "config" / f"{name}.json")


def load(project_id):
    d = ROOT / "projects" / project_id
    if not d.is_dir():
        known = sorted(p.name for p in (ROOT / "projects").iterdir() if p.is_dir())
        raise ProjectError(f"プロジェクト {project_id} がありません（既知: {', '.join(known)}）")
    p = _read(d / "project.json")
    missing = [k for k in REQUIRED_KEYS if k not in p]
    if missing:
        raise ProjectError(f"{d / 'project.json'} に必須キーがありません: {', '.join(missing)}")
    if p["id"] != project_id:
        raise ProjectError(f"{d / 'project.json'} の id が {p['id']} です（ディレクトリ名は {project_id}）")
    p["dir"] = str(d)
    return p


def pipeline_config(project):
    """pipeline.json に、リポジトリの位置を project.json から差し込んで返す。"""
    cfg = _read(Path(project["dir"]) / "pipeline.json")
    paths = cfg.setdefault("paths", {})
    for k in ("repo", "unity_project_subdir"):
        if k in paths:
            raise ProjectError(f"pipeline.json の paths.{k} は project.json にだけ書いてください")
    paths["repo"] = project["repo_dir"]
    paths["unity_project_subdir"] = project["unity_project_subdir"]
    return cfg
