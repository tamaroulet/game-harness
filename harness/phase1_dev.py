"""フェーズ 1 の公開の dev の並び（config/phase1_dev.json。実験と計画 §6-2）と、dev の外を走らせない見張り。

held-out の誤走を機械で防ぐ（操縦士の承認、2026-11-20。0b で、分ける前に採点に通したゲームの 1 つが held-out の側だった）。
実行口は、走りの前にここで照らし、dev の外なら何も作らずに止まる。止めたときは、held-out の名前を画面に出さないため、
渡された名前を文に入れない。held-out の区画を開けるときの口は、その段の計画とともに作る（実験と計画 §6-9）。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "phase1_dev.json"


class NotDevError(RuntimeError):
    pass


def load(path=CONFIG):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def require_scb(problem, path=CONFIG):
    """SlopCodeBench の問題が公開の dev にあるか。無ければ NotDevError。"""
    if problem not in load(path)["slopcodebench"]:
        raise NotDevError("公開の dev（config/phase1_dev.json）の外の問題は走らせない（held-out の誤走の防止。実験と計画 §6-2）")
    return problem


def require_mode4(game, case_id, path=CONFIG):
    """Mode 4 のゲームとケースの組が公開の dev にあるか。無ければ NotDevError。"""
    if case_id not in (load(path)["swe_game_mode4"].get(game) or []):
        raise NotDevError("公開の dev（config/phase1_dev.json）の外のゲームかケースは走らせない（held-out の誤走の防止。"
                          "実験と計画 §6-2）")
    return game, case_id
