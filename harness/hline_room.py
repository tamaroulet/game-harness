"""H ライン（harness/hline.py）の総監督の部屋：受信箱の設定（.claude/settings.json）と CLAUDE.md を書く。"""
import json
import shutil
from pathlib import Path

from hline_base import RESERVED, ROOT
from hline_queue import load_state
from hline_report import write_report

TEMPLATE = ROOT / "harness" / "templates" / "director_room" / "CLAUDE.md"


def director_settings(inbox, src_root=Path("C:/src"), home_dirs=(".claude", ".gemini")):
    """総監督の設定：シェルを無効にし、受信箱の外と自分の設定・H ラインの書くファイルへの書き込みを拒否する。deny は
    allow より強く「受信箱だけ許す」と書けないので、受信箱の外を作った時点の一覧で全部拒否する。"""
    inbox = Path(inbox)
    rule = lambda p: "//" + p.as_posix().replace(":", "", 1).lower() + "/**"  # noqa: E731

    outside = [p for p in sorted(Path(src_root).iterdir()) if p != inbox.parent]
    outside += [p for p in sorted(inbox.parent.iterdir()) if p != inbox]
    edit = [rule(p) if p.is_dir() else rule(p)[:-3] for p in outside] + [f"~/{d}/**" for d in home_dirs]
    edit += [rule(inbox / ".claude")] + [rule(inbox).replace("/**", "/" + n) for n in sorted(RESERVED)]
    read = [rule(Path(src_root) / ".local" / d) for d in ("wt", "out")] + ["**/*.cs"]
    deny = (["Bash", "PowerShell", "NotebookEdit", "mcp__terminal"]
            + [f"Edit({r})" for r in edit] + [f"Read({r})" for r in read])
    return {"permissions": {"deny": deny, "disableBypassPermissionsMode": "disable"}}


def setup(cfg):
    inbox = Path(cfg["inbox"])
    (inbox / ".claude").mkdir(parents=True, exist_ok=True)
    Path(cfg["out"]).mkdir(parents=True, exist_ok=True)
    (inbox / ".claude" / "settings.json").write_text(
        json.dumps(director_settings(inbox), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(TEMPLATE, inbox / "CLAUDE.md")
    (inbox / "TODO.md").touch()
    write_report(cfg, load_state(cfg))
    print(f"受信箱と総監督の部屋を書きました: {inbox}")
    return 0
