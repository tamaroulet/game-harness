"""登録されたプロジェクト（projects/*）の設定の検査。

    python -m unittest discover -s tests -v

- どのプロジェクトも、scheduler・pipeline・oracle が読む形で読み込めること
- 作業場と出力先を、プロジェクト間で共有していないこと
  pipeline.py は paths.sandbox が無いと unity-2d の ms3-sandbox を使う。2 本目のゲーム（falling-blocks）の
  登録で書き忘れると、2 つのゲームが同じサンドボックスを reset し合う。黙って既定値に落ちないよう、明示を要求する
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))

import oracle  # noqa: E402
import project  # noqa: E402
import scheduler as ms4  # noqa: E402

# プロジェクトごとに別でなければならない場所（(表示名, 値を取り出す関数)）
ISOLATED = [
    ("project.json repo_dir", lambda p, c: p.get("repo_dir")),
    ("project.json out_dir", lambda p, c: p.get("out_dir")),
    ("pipeline.json paths.out_dir", lambda p, c: c["paths"].get("out_dir")),
    ("pipeline.json paths.sandbox", lambda p, c: c["paths"].get("sandbox")),
    ("pipeline.json paths.playtest_worktree", lambda p, c: c["paths"].get("playtest_worktree")),
]


def norm(path):
    """Windows のパスとして比べる（区切り・大文字小文字・末尾の区切りを畳む）。"""
    return path.replace("/", "\\").rstrip("\\").lower()


def isolation_problems(loaded):
    """loaded: {id: (project.json, pipeline_config)}。問題の一覧（空なら OK）。"""
    problems, seen = [], {}
    for pid, (p, c) in sorted(loaded.items()):
        for label, get in ISOLATED:
            value = get(p, c)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{pid}: {label} がありません（既定値に落とさない）")
                continue
            owner = seen.setdefault((label, norm(value)), pid)
            if owner != pid:
                problems.append(f"{pid}: {label} が {owner} と同じです: {value}")
    return problems


def load_all():
    ids = sorted(d.name for d in (ROOT / "projects").iterdir() if (d / "project.json").is_file())
    loaded = {}
    for pid in ids:
        p = project.load(pid)
        loaded[pid] = (p, project.pipeline_config(p))
    return loaded


class TestRegisteredProjects(unittest.TestCase):
    def test_known_projects_are_registered(self):
        self.assertTrue({"unity-2d", "falling-blocks"} <= set(load_all()))

    def test_every_project_loads_for_scheduler_pipeline_and_oracle(self):
        for pid, (p, c) in load_all().items():
            with self.subTest(project=pid):
                cfg = ms4.build_config(pid)
                self.assertEqual(cfg["repo_slug"], p["repo_slug"])
                self.assertIn("approval", cfg["required_checks"])
                oracle.load(c.get("control_groups"), c.get("oracle"))
                for key in ("git", "gh", "implementer", "fast_tests", "engine_tests", "playtest_build"):
                    self.assertIsInstance(c["ttl_seconds"][key], int, f"ttl_seconds.{key}")

    def test_repo_slugs_are_unique(self):
        slugs = [p["repo_slug"].lower() for p, _ in load_all().values()]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_workspaces_are_not_shared(self):
        self.assertEqual(isolation_problems(load_all()), [])


class TestIsolationCheck(unittest.TestCase):
    """検査そのものが空振りしないこと（変異）。"""

    def fixture(self):
        def one(name):
            p = {"repo_dir": f"C:\\src\\{name}", "out_dir": f"C:\\src\\.local\\out\\harness\\{name}"}
            c = {"paths": {"out_dir": f"C:\\src\\.local\\out\\{name}",
                           "sandbox": f"C:\\src\\.local\\wt\\{name}-sandbox",
                           "playtest_worktree": f"C:\\src\\.local\\wt\\{name}-playtest"}}
            return p, c
        return {"a": one("a"), "b": one("b")}

    def test_clean_fixture_passes(self):
        self.assertEqual(isolation_problems(self.fixture()), [])

    def test_missing_sandbox_is_reported(self):
        loaded = self.fixture()
        del loaded["b"][1]["paths"]["sandbox"]
        self.assertEqual(len(isolation_problems(loaded)), 1)
        self.assertIn("paths.sandbox がありません", isolation_problems(loaded)[0])

    def test_shared_sandbox_is_reported_even_if_spelled_differently(self):
        loaded = self.fixture()
        loaded["b"][1]["paths"]["sandbox"] = "c:/SRC/.local/wt/a-sandbox/"
        problems = isolation_problems(loaded)
        self.assertEqual(len(problems), 1)
        self.assertIn("a と同じ", problems[0])

    def test_every_isolated_key_is_checked(self):
        for label, _ in ISOLATED:
            with self.subTest(key=label):
                loaded = self.fixture()
                key = label.rsplit(" ", 1)[1].split(".")[-1]
                side = 1 if label.startswith("pipeline.json") else 0
                src, dst = loaded["a"][side], loaded["b"][side]
                if side == 1:
                    src, dst = src["paths"], dst["paths"]
                dst[key] = src[key]
                self.assertTrue(isolation_problems(loaded), label)


if __name__ == "__main__":
    unittest.main()
