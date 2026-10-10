# -*- coding: utf-8 -*-
"""harness.probe - Extract minimal, high-signal objective state for director session."""
import os
import sys
import argparse
import subprocess
from pathlib import Path

def get_git_info(repo_dir: Path, log_count: int = 1) -> dict:
    info = {"branch": "unknown", "head": "unknown", "commit_msg": "", "dirty": False, "recent_logs": []}
    try:
        info["branch"] = subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "--abbrev-ref", "HEAD"],
            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        ).strip()
        info["head"] = subprocess.check_output(
            ["git", "-C", str(repo_dir), "rev-parse", "--short", "HEAD"],
            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        ).strip()
        info["commit_msg"] = subprocess.check_output(
            ["git", "-C", str(repo_dir), "log", "-1", "--pretty=%s"],
            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "-C", str(repo_dir), "status", "--porcelain"],
            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        ).strip()
        info["dirty"] = bool(status)
        if log_count > 1:
            logs = subprocess.check_output(
                ["git", "-C", str(repo_dir), "log", f"-{log_count}", "--oneline"],
                encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
            ).strip().splitlines()
            info["recent_logs"] = logs
    except Exception:
        pass
    return info

def get_env_info() -> dict:
    info = {
        "python": sys.version.split()[0],
        "os": sys.platform,
        "mujoco": "not installed"
    }
    try:
        import mujoco
        info["mujoco"] = getattr(mujoco, "__version__", "installed")
    except ImportError:
        pass
    return info

def get_progress_info(repo_dir: Path) -> dict:
    info = {"active_task_id": "none", "active_task_title": "", "active_group": "", "uncompleted_count": 0}
    try:
        from harness import progress as p
        path = repo_dir / p.REL_PATH
        if path.exists():
            data = p.load(path)
            info["active_task_id"] = data.get("active_task_id", "none")
            tasks = data.get("tasks", [])
            active_list = [t for t in tasks if t.get("id") == info["active_task_id"]]
            if active_list:
                t = active_list[0]
                info["active_task_title"] = t.get("title", "")
                info["active_group"] = t.get("group", "")
            uncompleted = [t for t in tasks if t.get("status") != "completed"]
            info["uncompleted_count"] = len(uncompleted)
    except Exception:
        pass
    return info

def get_modules_summary(repo_dir: Path) -> dict:
    harness_files = sorted([f.name for f in (repo_dir / "harness").glob("*.py") if not f.name.startswith("__")])
    test_files = sorted([f.name for f in (repo_dir / "tests").glob("*.py") if not f.name.startswith("__")])
    return {
        "harness_count": len(harness_files),
        "test_count": len(test_files),
        "all_tests": test_files
    }

def format_probe_output(repo_dir: Path, args: argparse.Namespace = None) -> str:
    args = args or argparse.Namespace(tests=False, git=False)
    git_info = get_git_info(repo_dir, log_count=5 if getattr(args, "git", False) else 1)
    env_info = get_env_info()
    prog_info = get_progress_info(repo_dir)
    mod_info = get_modules_summary(repo_dir)

    lines = [
        "================================================================================",
        "JIT OBJECTIVE STATE PROBE (Single Source of Truth Snapshot)",
        "================================================================================",
        f"[GIT HEAD] Branch: {git_info['branch']} | Commit: {git_info['head']} | Dirty: {git_info['dirty']}",
        f"           Latest Message: {git_info['commit_msg']}  (Source: git log -1)",
        f"[ENVIRONMENT] Python: {env_info['python']} | OS: {env_info['os']} | MuJoCo: {env_info['mujoco']}  (Source: sys/mujoco)",
        f"[CODEBASE STRUCTURE] harness/*.py: {mod_info['harness_count']} files | tests/*.py: {mod_info['test_count']} files",
        f"[PROGRESS STATUS] Active Task: {prog_info['active_task_id']} (Group: {prog_info['active_group']})",
        f"                  Title: {prog_info['active_task_title']}",
        f"                  Pending Tasks Count: {prog_info['uncompleted_count']}  (Source: docs/progress.yaml via harness.progress)",
        "--------------------------------------------------------------------------------",
        "[OPERATIONAL BOUNDARIES & CONSTRAINTS]",
        "- Role: You are the High-Level Director. Define 'Goal', 'Rough Steps', 'Verify Command'.",
        "- Forbidden: Direct micro code editing, game design lore/fluff, coinages/jargon.",
        "- Ground Truth: Fastsuite regression (1,344 tests) + Verified Receipt execution.",
        "================================================================================"
    ]

    if getattr(args, "git", False) and git_info.get("recent_logs"):
        lines.append("[ON-DEMAND: RECENT GIT LOGS]")
        for l in git_info["recent_logs"]:
            lines.append(f"  {l}")

    if getattr(args, "tests", False):
        lines.append(f"[ON-DEMAND: ALL TEST FILES ({len(mod_info['all_tests'])} files)]")
        for i in range(0, len(mod_info["all_tests"]), 4):
            lines.append("  " + ", ".join(mod_info["all_tests"][i:i+4]))

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="Extract minimal objective state for director session.")
    parser.add_argument("--tests", action="store_true", help="Include all test file names on-demand")
    parser.add_argument("--git", action="store_true", help="Include recent git commit history on-demand")
    args = parser.parse_args()

    repo_dir = Path(__file__).resolve().parent.parent
    output = format_probe_output(repo_dir, args)
    
    # Safe output for Windows console
    if sys.stdout.encoding and sys.stdout.encoding.lower() in ["cp932", "shift_jis"]:
        print(output.encode("cp932", errors="replace").decode("cp932"))
    else:
        print(output)

if __name__ == "__main__":
    main()
