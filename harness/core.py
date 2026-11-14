"""制御核（設計 §4-2・§5-1）：要件の台帳・検証の手順の形・1 つの単位を 1 つの会話で進める周回・検証の背圧の関門。

制御核は、どの実行環境で、どのモデルが動くかを知らない。コマンドの文字列・パスの形・OS を持たない。外とは 2 つの口だけで繋ぐ。
- 実行環境のアダプタ：verify(checks) -> [CheckResult]。検証の手順（Check の並び）を走らせ、決まった形で返す。
- エージェントの実行口：start(prompt, budget_s) -> Turn と send(text, budget_s) -> Turn。会話を始める・続ける。
  Turn は、その呼び出しの利用量と、CLI が報告したモデルを持つ。

決まり：
- 台帳の状態は、実装役の宣言でなく、関門の結果だけで変わる（設計 §4-3）。
- 押し返しの文は、落ちた確かめの名と 1 行の要約を機械的に並べるだけにする。頼みの文面のお願いを足さない（憲章 §5）。
- 細かいリセットはしない。押し返しは同じ会話に送る（設計 §4-4、H5）。
- 報告されたモデルが名指しと違う手番が出たら、その場で止める（CLAUDE.md のモデルの固定）。

部品は設定で 1 つずつ足し外しできる（X-02 の H2〜H5）：
- show_ledger：はじめの頼みに台帳を添える（入口の台帳）。
- gate：手番の後に確かめを走らせ、通るまで押し返す（出口の背圧）。切ると、1 回の手番で終える（素の A と同じ形）。
- max_rounds：押し返しを含む手番の数の上限。
"""
import json
import time
from dataclasses import asdict, dataclass, field

MIN_TURN_S = 120          # 残りの時間がこれより短ければ、新しい手番を始めない
FEEDBACK_ITEMS = 25       # 押し返しの文に並べる、落ちた項目の数の上限
FEEDBACK_LINE = 300       # 落ちの 1 行の要約の長さの上限


@dataclass
class Check:
    """検証の手順の 1 つ。kind と target の意味はアダプタが決める（制御核は解釈しない）。"""
    id: str
    kind: str
    target: list
    required: bool = True


@dataclass
class CheckResult:
    """確かめの結果の形。passed・failed は項目の名（テストの名など）、errors は「名: 1 行の要約」。"""
    id: str
    ok: bool
    passed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    summary: str = ""
    seconds: float = 0.0
    raw: str = ""


@dataclass
class Requirement:
    """要件の 1 つ。checks は、この要件を確かめる「確かめの id」か「確かめの id::項目の名」の並び。"""
    id: str
    source: str
    text: str
    checks: list
    status: str = "open"


@dataclass
class Ledger:
    unit: str
    requirements: list
    checks: list

    def to_dict(self):
        return {"unit": self.unit, "requirements": [asdict(r) for r in self.requirements],
                "checks": [asdict(c) for c in self.checks]}

    @classmethod
    def from_dict(cls, d):
        return cls(unit=d["unit"], requirements=[Requirement(**r) for r in d["requirements"]],
                   checks=[Check(**c) for c in d["checks"]])


@dataclass
class Turn:
    """実行口の 1 回の呼び出し。ok は、エージェントが手番を終えて答えを返したか（打ち切り・誤りなら False）。"""
    ok: bool
    model: str
    usage: dict
    seconds: float
    response: str = ""
    conversation_id: str = ""
    note: str = ""


def decide(checks, results):
    """合否：必須の確かめが全部あり、全部 ok なら True。結果の無い必須の確かめは落ちに数える。"""
    by_id = {r.id: r for r in results}
    return all(by_id.get(c.id) is not None and by_id[c.id].ok for c in checks if c.required)


def update_ledger(ledger, results):
    """台帳の状態を、関門の結果だけで決め直す（open に戻ることもある）。"""
    by_id = {r.id: r for r in results}
    for req in ledger.requirements:
        done = bool(req.checks)
        for ref in req.checks:
            cid, _, item = ref.partition("::")
            res = by_id.get(cid)
            if res is None:
                done = False
            elif item:
                done = done and item in res.passed and item not in res.failed
            else:
                done = done and res.ok
        req.status = "done" if done else "open"


def render_ledger(ledger):
    """はじめの頼みに添える台帳（エージェント向けの英文）。状態の欄は関門の結果で決まることを書く。"""
    lines = ["Requirements ledger for this task (status is set only by the harness's verification runs):"]
    for req in ledger.requirements:
        mark = "x" if req.status == "done" else " "
        lines.append(f"- [{mark}] {req.id} ({req.source}): {req.text}")
    lines.append("Verification steps the harness runs after each of your turns:")
    for c in ledger.checks:
        need = "required" if c.required else "advisory"
        lines.append(f"- {c.id} [{c.kind}, {need}]: {', '.join(map(str, c.target)) or '(changed files)'}")
    return "\n".join(lines)


def feedback(checks, results):
    """押し返しの文（エージェント向けの英文）。落ちた確かめごとに、数・落ちた項目・1 行の要約を機械的に並べる。"""
    by_id = {r.id: r for r in results}
    lines = ["Harness verification after your last turn: FAILED."]
    for c in checks:
        r = by_id.get(c.id)
        if r is None:
            lines.append(f"- {c.id} [{c.kind}]: no result")
            continue
        if r.ok:
            lines.append(f"- {c.id} [{c.kind}]: passed ({r.summary or len(r.passed)})")
            continue
        need = "" if c.required else " (advisory)"
        lines.append(f"- {c.id} [{c.kind}]{need}: failed ({r.summary or f'{len(r.failed)} failed'})")
        shown = r.errors[:FEEDBACK_ITEMS] if r.errors else [f"{name}" for name in r.failed[:FEEDBACK_ITEMS]]
        lines += [f"  - {e[:FEEDBACK_LINE]}" for e in shown]
        rest = max(len(r.errors or r.failed) - FEEDBACK_ITEMS, 0)
        if rest:
            lines.append(f"  - ... and {rest} more")
    return "\n".join(lines)


def run_unit(port, adapter, ledger, prompt, *, expected_model, budget_s, gate=True, show_ledger=False,
             max_rounds=4, clock=time.monotonic):
    """1 つの単位を 1 つの会話で進める周回。返すのは記録（JSON にできる dict）。

    手順：頼み（show_ledger なら台帳を添える）で会話を始める → 手番が終わるたびに、gate なら確かめを走らせて台帳を更新
    → 合格なら終わる → 落ちていて、手番の数と時間が残っていれば、押し返しの文を同じ会話に送る。
    止まる理由（stop）：pass・no_gate・max_rounds・budget・agent_error・model_mismatch。
    """
    t0 = clock()
    record = {"unit": ledger.unit, "settings": {"gate": gate, "show_ledger": show_ledger, "max_rounds": max_rounds,
                                                "budget_s": budget_s, "expected_model": expected_model},
              "rounds": [], "decision": None, "stop": None}

    def left():
        return budget_s - (clock() - t0)

    text = prompt + ("\n\n" + render_ledger(ledger) if show_ledger else "")
    turn = port.start(text, left())
    while True:
        rnd = {"turn": asdict(turn), "verify": None, "passed": None}
        record["rounds"].append(rnd)
        if turn.model != expected_model:
            record["stop"] = "model_mismatch"
            break
        if not turn.ok:
            record["stop"] = "agent_error"
            break
        if not gate:
            record["stop"] = "no_gate"
            break
        results = adapter.verify(ledger.checks)
        update_ledger(ledger, results)
        passed = decide(ledger.checks, results)
        rnd["verify"] = [asdict(r) for r in results]
        rnd["passed"] = passed
        if passed:
            record["stop"] = "pass"
            break
        if len(record["rounds"]) >= max_rounds:
            record["stop"] = "max_rounds"
            break
        if left() < MIN_TURN_S:
            record["stop"] = "budget"
            break
        turn = port.send(feedback(ledger.checks, results), left())
    last = [r for r in record["rounds"] if r["passed"] is not None]
    record["decision"] = "pass" if last and last[-1]["passed"] else ("unverified" if not last else "fail")
    record["seconds"] = round(clock() - t0, 1)
    record["ledger"] = ledger.to_dict()
    return record


def total_usage(record):
    """記録の手番の利用量を足し合わせる（各呼び出しの利用量は手番の足し込み。会話の累計は足さない）。"""
    keys = ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "model_steps", "lost_steps")
    out = {k: 0 for k in keys}
    partial = False
    for rnd in record.get("rounds", []):
        u = (rnd.get("turn") or {}).get("usage") or {}
        for k in keys:
            if isinstance(u.get(k), int):
                out[k] += u[k]
        partial = partial or bool(u.get("partial"))
    out["partial"] = partial
    out["turns"] = len(record.get("rounds", []))
    return out


def dumps(record):
    return json.dumps(record, ensure_ascii=False, indent=1)
