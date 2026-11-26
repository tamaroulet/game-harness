"""制御核（設計 §4-2・§5-1）：要件の台帳・検査の手順の形・1 つの単位を 1 つの会話で進める周回・単位の列を積み重ねる周回・
2 つの関門・安全停止（H_stop_1）。

制御核は、どの実行環境で、どのモデルが動くかを知らない。コマンドの文字列・パスの形・OS を持たない。外とは 3 つの口だけで繋ぐ。
- 実行環境のアダプタ：verify(checks) -> [CheckResult]。検査の手順（Check の並び）を走らせ、決まった形で返す。
  任意の口（無ければその部品を使わない）：fingerprint()（作業場の状態の SHA-256。同じ差分の連続の検知）・changes()（単位の
  始まりからの変更の範囲）・checkpoint(label) と restore(label)（確定とロールバック）・write_handoff(text, data)（構造化の
  引き継ぎを作業場に置く）・guard(checks) と protects（守る検査の元のファイルを記録し、kind "protect" の検査で改変を見る）
- エージェントの実行口：start(prompt, budget_s) -> Turn と send(text, budget_s) -> Turn。会話を始める・続ける。
  Turn は、その呼び出しの利用量と、CLI が報告したモデルを持つ
- 第 2 関門（差し替え口）：prepare(ledger, checks) -> [Check]。第 1 関門を初めて通った区切りの出口で 1 回だけ呼び、外からの
  実行検証（契約）と不変条件・性質の検査を返す。制御核はそれを固め、押し返しの周回では当て直すだけで呼び直さない（K#C8）。
  反例・契約違反が出たときだけ押し返す。仕様だけから細かい単体テストを作る形（SWT-Bench の型）は採らない

決まり：
- 検査の結果は pass・fail・infra の 3 値（設計 §5-1 の契約）。infra（測定系の異常）はエージェントの落ちに数えず、押し返さない。
  1 回だけやり直し、続けば止めて理由を残す
- 台帳の状態は、実装役の宣言でなく、関門の結果だけで変わる（設計 §4-3）。非退行の検査の改廃（supersede）も、上流の台帳が
  単位ごとに宣言するもので、実装役の宣言では変えない
- 押し返しの文は、落ちた検査の名と 1 行の要約を機械的に並べるだけにする。プロンプトにお願いを足さない（憲章 §5）
- 細かいリセットはしない。押し返しは同じ会話に送る（設計 §4-4）
- 報告されたモデルが名指しと違うターンが出たら、その場で止める（CLAUDE.md のモデルの固定）

部品は設定で 1 つずつ足し外しできる：
- show_ledger：はじめのプロンプトに台帳を添える（入口の台帳）
- gate：ターンの後に検査を走らせ、通るまで押し返す（出口の関門）。切ると、1 回のターンで終える（素の A と同じ形）
- max_rounds：押し返しを含むターンの数の上限
- stop_rules：H_stop_1 の発火の線（StopWatch）
- second_gate：第 2 関門
- Chain：context（単位の間の文脈）・rollback（落ちた単位を始まりに戻す）・halt_on_stop（合格せずに止まった単位で列を止め、
  人の裁定を待つ）
"""
import copy
import hashlib
import json
import time
from dataclasses import asdict, dataclass, field

MIN_TURN_S = 120          # 残りの時間がこれより短ければ、新しいターンを始めない
FEEDBACK_ITEMS = 25       # 押し返しの文に並べる、落ちた項目の数の上限
FEEDBACK_LINE = 300       # 落ちの 1 行の要約の長さの上限

PASS, FAIL, INFRA = "pass", "fail", "infra"
STATUSES = (PASS, FAIL, INFRA)
# 検査の役割：gate＝単位の第 1 関門・regression＝前の単位で通った項目（非退行の関門）・protect＝守る検査の元のファイルの
# 改変の検知・second＝第 2 関門（外からの実行検証と不変条件）
GATE, REGRESSION, PROTECT, SECOND = "gate", "regression", "protect", "second"
ROLES = (GATE, REGRESSION, PROTECT, SECOND)
PROTECT_ID = "protect"
# 単位の間の文脈：continue＝同じ会話に次の単位のプロンプトを送る（Harbor の --resume-trajectory に当たる）・handoff＝新しい
# 会話に、ハーネスが検査の記録から作る構造化の引き継ぎ（WORKSPACE_HANDOFF.md と進捗の JSON）を添える・fresh＝新しい会話だけ
# （Harbor の既定）
CONTEXTS = ("continue", "handoff", "fresh")
HANDOFF_FILE = "WORKSPACE_HANDOFF.md"
H_STOP = "h_stop_1"
UNMET_STOPS = (H_STOP, "max_rounds", "budget", INFRA)   # 合格せずに止まり、人の再開の要約を残す止まり方
CHOICES = ("discard", "fix_and_resume", "change_spec")   # H_stop_1 の後に人が選ぶもの（破棄・修正して再開・仕様の変更）


@dataclass
class Check:
    """検査の手順の 1 つ。kind と target の意味はアダプタが決める（制御核は解釈しない）。items が空でなければ、合格は
    「items がどれも通ったこと」（非退行の検査は、確定のときに通った項目を持つ）。origin は検査を足した単位。"""
    id: str
    kind: str
    target: list
    required: bool = True
    role: str = GATE
    items: list = field(default_factory=list)
    origin: str = ""

    def __post_init__(self):
        if self.role not in ROLES:
            raise ValueError(f"検査の役割が違う: {self.role!r}")


@dataclass
class CheckResult:
    """検査の結果の形。status は pass・fail・infra（測定系の異常）。ok は status が pass のときだけ True（status を渡さない
    呼び出しは ok から決める）。passed・failed は項目の名（テストの名など）、errors は「名: 1 行の要約」。skipped は、
    検査の items のうちアダプタが測りの決まりで数えないとした項目（単位の始まりから落ちていた・消えた。理由は summary）。"""
    id: str
    ok: bool = False
    passed: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    summary: str = ""
    seconds: float = 0.0
    raw: str = ""
    status: str = ""
    skipped: list = field(default_factory=list)

    def __post_init__(self):
        if not self.status:
            self.status = PASS if self.ok else FAIL
        if self.status not in STATUSES:
            raise ValueError(f"検査の結果の値が違う: {self.status!r}")
        self.ok = self.status == PASS


@dataclass
class Requirement:
    """要件の 1 つ。checks は、この要件を検査する「検査の id」か「検査の id::項目の名」の並び。"""
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
    """実行口の 1 回の呼び出し。ok は、エージェントがターンを終えて答えを返したか（打ち切り・時間切れ・誤りなら False）。
    実行口が走りの後の検査（モデルの照合・止めた道具・フック）を持つなら、それに落ちたターンも False にし、理由を note に書く
    （harness/agy_port.py）。"""
    ok: bool
    model: str
    usage: dict
    seconds: float
    response: str = ""
    conversation_id: str = ""
    note: str = ""


@dataclass
class Unit:
    """積み重ねの 1 単位：台帳・プロンプト・この単位で改廃する非退行の検査（supersede。「検査の id」か「検査の id::項目」）。"""
    ledger: Ledger
    prompt: str
    supersedes: list = field(default_factory=list)


# ---------------------------------------------------------------- 合否

def check_status(check, result):
    """1 つの検査の結果の値。結果が無ければ fail（必須の検査の結果の欠けは落ちに数える）。items があれば項目で読む
    （アダプタが数えないとした skipped の項目は除く）。"""
    if result is None:
        return FAIL
    if result.status == INFRA:
        return INFRA
    if check.items:
        need = [i for i in check.items if i not in result.skipped]
        return PASS if all(i in result.passed and i not in result.failed for i in need) else FAIL
    return result.status


def verdict(checks, results):
    """合否の 3 値：必須の検査に infra があれば infra、必須の検査が全部 pass なら pass、それ以外は fail。"""
    by_id = {r.id: r for r in results}
    got = [check_status(c, by_id.get(c.id)) for c in checks if c.required]
    if INFRA in got:
        return INFRA
    return PASS if all(s == PASS for s in got) else FAIL


def decide(checks, results):
    """合否：必須の検査が全部あり、全部 pass なら True。"""
    return verdict(checks, results) == PASS


def failing_items(check, result):
    """落ちている項目の名（items があれば通っていない items、無ければ結果の failed）。"""
    if result is None:
        return []
    if check.items:
        return [i for i in check.items if i not in result.skipped and (i not in result.passed or i in result.failed)]
    return list(result.failed)


def failing_count(checks, results):
    """必須の検査の落ちの数（落ちた項目の数。項目の無い落ちは 1）。前進の物差し。"""
    by_id = {r.id: r for r in results}
    n = 0
    for c in checks:
        if c.required and check_status(c, by_id.get(c.id)) != PASS:
            n += max(len(failing_items(c, by_id.get(c.id))), 1)
    return n


def update_ledger(ledger, results):
    """台帳の状態を、関門の結果だけで決め直す（open に戻ることもある。infra は open）。"""
    by_id = {r.id: r for r in results}
    for req in ledger.requirements:
        done = bool(req.checks)
        for ref in req.checks:
            cid, _, item = ref.partition("::")
            res = by_id.get(cid)
            if res is None:
                done = False
            elif item:
                done = done and res.status != INFRA and item in res.passed and item not in res.failed
            else:
                done = done and res.ok
        req.status = "done" if done else "open"


def render_ledger(ledger):
    """はじめのプロンプトに添える台帳（エージェント向けの英文）。状態の欄は関門の結果で決まることを書く。"""
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
    """押し返しの文（エージェント向けの英文）。落ちた検査ごとに、数・落ちた項目・1 行の要約を機械的に並べる。"""
    by_id = {r.id: r for r in results}
    lines = ["Harness verification after your last turn: FAILED."]
    for c in checks:
        r = by_id.get(c.id)
        tag = c.kind if c.role == GATE else f"{c.kind}, {c.role}"
        if r is None:
            lines.append(f"- {c.id} [{tag}]: no result")
            continue
        st = check_status(c, r)
        if st == PASS:
            lines.append(f"- {c.id} [{tag}]: passed ({r.summary or len(r.passed)})")
            continue
        need = "" if c.required else " (advisory)"
        if st == INFRA:
            lines.append(f"- {c.id} [{tag}]{need}: not measured (infrastructure: {(r.summary or 'error')[:FEEDBACK_LINE]})")
            continue
        bad = failing_items(c, r)
        lines.append(f"- {c.id} [{tag}]{need}: failed ({r.summary or f'{len(bad)} failed'})")
        if c.items:
            shown = [e for e in r.errors if any(e.startswith(i) for i in bad)] or bad
        else:
            shown = r.errors or bad
        lines += [f"  - {e[:FEEDBACK_LINE]}" for e in shown[:FEEDBACK_ITEMS]]
        rest = max(len(shown) - FEEDBACK_ITEMS, 0)
        if rest:
            lines.append(f"  - ... and {rest} more")
    return "\n".join(lines)


def verify_checks(adapter, checks, infra_retries=1):
    """アダプタで検査を走らせる。必須の検査に infra があれば infra_retries 回までやり直す。(結果, 合否, やり直しの記録)。"""
    attempts = []
    results = adapter.verify(checks)
    v = verdict(checks, results)
    while v == INFRA and len(attempts) < infra_retries:
        attempts.append([{"id": r.id, "summary": r.summary} for r in results if r.status == INFRA])
        results = adapter.verify(checks)
        v = verdict(checks, results)
    return results, v, attempts


def _optional(adapter, name, *args):
    fn = getattr(adapter, name, None)
    return fn(*args) if callable(fn) else None


# ---------------------------------------------------------------- 安全停止（H_stop_1）

class StopWatch:
    """H_stop_1 の発火の線を周回ごとに見る（客観の値だけ。実験と計画 §6-7）。rules の値が 0 か None の線は見ない。
    - same_diff：作業場の状態の SHA-256 が、rules["same_diff"] 回続けて前のターンと同じ（単位の始まりの値も数える）
    - no_progress：必須の検査の落ちの数が、rules["no_progress"] 回続けて最小を更新しない（検査の組が変われば数え直す）。
      前の単位で通った項目を続けて壊す落ちも、ここで数える
    - protect：守る検査の元のファイルの改変の検知が rules["protect"] 回（1 回目は押し返しで知らせる）
    - second：第 2 関門の検査の落ちが rules["second"] 回（規定回数。止まったら不変条件の側の誤りの候補か実装の誤りかの要約）"""

    def __init__(self, rules=None, start_fp=None):
        self.rules = dict(rules or {})
        self.fps = [] if start_fp is None else [start_fp]
        self.best, self.stale, self.basis = None, 0, None
        self.protect, self.second = 0, 0

    def same_streak(self):
        n = 0
        for a, b in zip(reversed(self.fps), list(reversed(self.fps))[1:]):
            if a != b:
                break
            n += 1
        return n

    def see(self, fp, checks, results):
        """1 周回の結果を足し、発火すれば (線の名, 理由)、しなければ None。"""
        if fp is not None:
            self.fps.append(fp)
        by_id = {r.id: r for r in results}
        roles = {c.role for c in checks if c.required and check_status(c, by_id.get(c.id)) != PASS}
        self.protect += PROTECT in roles
        self.second += SECOND in roles
        basis = tuple(sorted(c.id for c in checks if c.required))
        n = failing_count(checks, results)
        if basis != self.basis:
            self.basis, self.best, self.stale = basis, n, 0
        elif n < self.best:
            self.best, self.stale = n, 0
        else:
            self.stale += 1
        r, same = self.rules, self.same_streak()
        if r.get("same_diff") and same >= r["same_diff"]:
            return "same_diff", f"作業場の状態の SHA-256 が {same} 回続けて前のターンと同じ"
        if r.get("protect") and self.protect >= r["protect"]:
            return "protect", f"守る検査の元のファイルの改変の検知が {self.protect} 回"
        if r.get("second") and self.second >= r["second"]:
            return "second_gate", f"第 2 関門の検査の落ちが {self.second} 回"
        if r.get("no_progress") and self.stale >= r["no_progress"]:
            return "no_progress", f"必須の検査の落ちの数が {self.stale} 回続けて減らない（最小 {self.best}）"
        return None


def _rounds_results(rounds):
    """周回の記録 → [(周回の番号, 状態の SHA-256, {検査の id: CheckResult})]（検査を走らせた周回だけ）。"""
    out = []
    for i, rnd in enumerate(rounds, 1):
        if rnd.get("verify") is not None:
            out.append((i, rnd.get("fingerprint"), {d["id"]: CheckResult(**d) for d in rnd["verify"]}))
    return out


def resume_summary(ledger, record, checks, changes=None):
    """合格せずに止まった単位の、人の再開の要約（決まった形。実験と計画 §6-7・Unity 通し穴 ③）。
    unmet は落ちている必須の検査ごとに、役割・落ちている項目・根拠の台帳の項目・落ちた周回の数・落ち方が毎回同じだったか・分類。
    分類は第 2 関門の検査だけに付ける：invariant_suspect（不変条件の側の誤りの候補：最後の周回で第 2 関門のほかの必須の検査は
    すべて通り、同じ項目が 2 周回以上、作業場が変わっても同じ形で落ちた）・implementation_bug（第 2 関門のほかの検査も落ちて
    いる、または落ち方が作業場の変化に連れて変わった）・undecided（周回が足りない、または作業場が変わっていない）。分類は人の
    裁定の材料で、制御核は自分で検査を外さない。"""
    seen = _rounds_results(record["rounds"])
    last = seen[-1][2] if seen else {}
    others_pass = all(check_status(c, last.get(c.id)) == PASS for c in checks if c.required and c.role != SECOND)
    unmet = []
    for c in checks:
        if not c.required or check_status(c, last.get(c.id)) == PASS:
            continue
        bad_rounds = [(fp, frozenset(failing_items(c, res[c.id])) or frozenset({"(check failed)"}))
                      for _, fp, res in seen if c.id in res and check_status(c, res[c.id]) != PASS]
        same = len(bad_rounds) >= 2 and len({b for _, b in bad_rounds}) == 1
        moved = len({fp for fp, _ in bad_rounds}) > 1
        cls = None
        if c.role == SECOND:
            if len(bad_rounds) < 2:
                cls = "undecided"
            elif not others_pass or not same:
                cls = "implementation_bug"
            elif moved:
                cls = "invariant_suspect"
            else:
                cls = "undecided"
        grounds = [r.id for r in ledger.requirements if any(ref.partition("::")[0] == c.id for ref in r.checks)]
        res = last.get(c.id)
        failing = failing_items(c, res)
        unmet.append({"check": c.id, "role": c.role, "kind": c.kind, "status": check_status(c, res),
                      "failing": failing[:FEEDBACK_ITEMS], "n_failing": len(failing), "grounds": grounds,
                      "errors": [e[:FEEDBACK_LINE] for e in (res.errors if res else [])[:5]],
                      "summary": (res.summary if res else "")[:FEEDBACK_LINE],
                      "rounds_failed": len(bad_rounds), "same_failure": same, "class": cls})
    return {"unit": ledger.unit, "stop": record["stop"], "rule": record.get("stop_rule"),
            "reason": record.get("stop_reason"), "rounds": len(record["rounds"]), "changes": changes,
            "unmet": unmet, "choices": list(CHOICES)}


# ---------------------------------------------------------------- 1 つの単位

def _freeze(checks, unit, taken):
    out = []
    for c in checks or []:
        c = copy.deepcopy(c)
        c.role, c.origin = SECOND, c.origin or unit
        if c.id in taken:
            raise ValueError(f"第 2 関門の検査の id が重なる: {c.id}")
        taken.add(c.id)
        out.append(c)
    return out


def _cover(checks, ids):
    """保護の検査（kind protect）の対象に ids を足す（無ければ作って checks に足す）。"""
    prot = next((c for c in checks if c.role == PROTECT), None)
    if prot is None:
        prot = Check(PROTECT_ID, "protect", [], role=PROTECT)
        checks.append(prot)
    prot.target = sorted(set(prot.target) | set(ids))


def run_unit(port, adapter, ledger, prompt, *, expected_model, budget_s, gate=True, show_ledger=False,
             max_rounds=4, clock=time.monotonic, opening="start", checks=None, second_gate=None, second_checks=None,
             stop_rules=None, infra_retries=1, protect=True):
    """1 つの単位を 1 つの会話で進める周回。返すのは記録（JSON にできる dict）。

    手順：プロンプト（show_ledger なら台帳を添える）を opening（start＝新しい会話・send＝今の会話の続き）で送る → ターンが
    終わるたびに、gate なら検査（checks。既定は台帳の検査）を走らせて台帳を更新 → 第 1 関門を初めて通ったら、second_gate の
    prepare を 1 回だけ呼び、返った検査を固めて当てる（second_checks を渡されたら、それを固めた検査として使い、prepare は
    呼ばない）→ 合格なら終わる → infra がやり直しの後も続けば止める → H_stop_1 の線に当たれば止める → 落ちていて、ターンの数と
    時間が残っていれば、押し返しの文を同じ会話に送る。
    止まる理由（stop）：pass・no_gate・max_rounds・budget・agent_error・model_mismatch・infra・h_stop_1。
    合格せずに止まった単位（UNMET_STOPS）には、人の再開の要約（resume）を付ける。
    protect を切ると、固めた検査の元のファイルを守らない（改廃の宣言の出どころが無い間の比較条件。Chain の protect）。
    """
    if opening not in ("start", "send"):
        raise ValueError(f"opening が違う: {opening!r}")
    t0 = clock()
    checks = list(ledger.checks if checks is None else checks)
    frozen = second_checks is not None
    second = [copy.deepcopy(c) for c in second_checks] if frozen else []
    protects = gate and protect and getattr(adapter, "protects", False)
    if second and protects:
        _cover(checks, [c.id for c in second])   # 固めたときに guard 済み（人の再開）
    record = {"unit": ledger.unit, "settings": {"gate": gate, "show_ledger": show_ledger, "max_rounds": max_rounds,
                                                "budget_s": budget_s, "expected_model": expected_model,
                                                "opening": opening, "stop_rules": dict(stop_rules or {}),
                                                "second_gate": second_gate is not None or frozen},
              "rounds": [], "decision": None, "stop": None}

    def left():
        return budget_s - (clock() - t0)

    watch = StopWatch(stop_rules, _optional(adapter, "fingerprint") if gate else None)
    text = prompt + ("\n\n" + render_ledger(ledger) if show_ledger else "")
    turn = (port.start if opening == "start" else port.send)(text, left())
    while True:
        rnd = {"turn": asdict(turn), "verify": None, "verdict": None, "passed": None}
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
        rnd["fingerprint"] = _optional(adapter, "fingerprint")
        run = checks + second
        results, v, retried = verify_checks(adapter, run, infra_retries)
        if v == PASS and not frozen and second_gate is not None:
            frozen = True
            second = _freeze(second_gate.prepare(ledger, run), ledger.unit, {c.id for c in run})
            rnd["second_gate"] = [c.id for c in second]
            if second:
                extra = list(second)
                if protects:
                    _optional(adapter, "guard", second)
                    _cover(checks, [c.id for c in second])
                    extra.append(next(c for c in checks if c.role == PROTECT))
                more, _, retried2 = verify_checks(adapter, extra, infra_retries)
                fresh = {r.id for r in more}
                results = [r for r in results if r.id not in fresh] + more
                retried += retried2
                run = checks + second
                v = verdict(run, results)
        rnd.update(verify=[asdict(r) for r in results], verdict=v, passed=v == PASS)
        if retried:
            rnd["infra_retries"] = retried
        if v == INFRA:
            record["stop"], record["stop_reason"] = INFRA, "測定系の異常が、やり直しの後も続いた"
            break
        update_ledger(ledger, results)
        if v == PASS:
            record["stop"] = "pass"
            break
        why = watch.see(rnd["fingerprint"], run, results)
        if why:
            record["stop"], (record["stop_rule"], record["stop_reason"]) = H_STOP, why
            break
        if len(record["rounds"]) >= max_rounds:
            record["stop"] = "max_rounds"
            break
        if left() < MIN_TURN_S:
            record["stop"] = "budget"
            break
        turn = port.send(feedback(run, results), left())
    seen = [r["verdict"] for r in record["rounds"] if r["verdict"] is not None]
    record["decision"] = {PASS: "pass", FAIL: "fail", INFRA: "infra"}[seen[-1]] if seen else "unverified"
    record["second_checks"] = [asdict(c) for c in second]
    if record["stop"] in UNMET_STOPS:
        record["resume"] = resume_summary(ledger, record, checks + second, _optional(adapter, "changes"))
    record["seconds"] = round(clock() - t0, 1)
    record["ledger"] = ledger.to_dict()
    return record


# ---------------------------------------------------------------- 積み重ね（単位の列）

def supersede(regression, refs, by):
    """非退行の検査の改廃（仕様の変更。P-042）。refs は「検査の id」（検査ごと外す）か「検査の id::項目」（項目を外し、項目が
    無くなれば検査ごと外す）。見つからない ref も found: False で記録に残す（打ち間違いを黙って通さない）。"""
    out = []
    for ref in refs or []:
        cid, _, item = ref.partition("::")
        c = next((x for x in regression if x.id == cid), None)
        if c is None or (item and item not in c.items):
            out.append({"ref": ref, "by": by, "found": False})
            continue
        if item:
            c.items.remove(item)
            if not c.items:
                regression.remove(c)
        else:
            regression.remove(c)
        out.append({"ref": ref, "by": by, "found": True})
    return out


def confirm(regression, checks, results, unit):
    """合格した単位の、通った必須の検査の項目を非退行の検査として積む。積むのは項目（テストの名など）のある検査だけ
    （項目の無い検査は、次の単位でもその単位の第 1 関門として走る）。足した検査を返す。"""
    by_id = {r.id: r for r in results}
    have = {c.id for c in regression}
    added = []
    for c in checks:
        r = by_id.get(c.id)
        if not c.required or c.role in (PROTECT, REGRESSION) or check_status(c, r) != PASS:
            continue
        items = sorted(set(c.items or r.passed))
        rid = f"{unit}:{c.id}"
        if items and rid not in have:
            added.append(Check(rid, c.kind, list(c.target), True, REGRESSION, items, c.origin or unit))
    regression.extend(added)
    return added


def render_handoff(records, regression, retired):
    """構造化の引き継ぎ（エージェント向けの英文と、進捗の JSON）。中身はハーネスの検査の記録だけから作る（実装役の宣言は
    入れない）。"""
    units = []
    for rec in records:
        unmet = [{"check": u["check"], "role": u["role"], "failing": u["failing"][:10], "n_failing": u["n_failing"],
                  "errors": (u.get("errors") or [])[:3]}
                 for u in (rec.get("resume") or {}).get("unmet", [])]
        units.append({"unit": rec["unit"], "decision": rec["decision"], "stop": rec["stop"], "turns": len(rec["rounds"]),
                      "open_requirements": [r["id"] for r in rec["ledger"]["requirements"] if r["status"] != "done"],
                      "unmet": unmet})
    data = {"file": HANDOFF_FILE, "units": units,
            "regression": [{"id": c.id, "kind": c.kind, "items": len(c.items), "origin": c.origin} for c in regression],
            "retired": [r for r in retired if r.get("found")]}
    lines = [f"Harness handoff ({HANDOFF_FILE}). Written by the harness from its verification records of the previous "
             "steps; the previous conversation is not carried over.", "Previous steps:"]
    for u in units:
        lines.append(f"- {u['unit']}: {u['decision']} (stop: {u['stop']}, turns: {u['turns']})")
        for m in u["unmet"]:
            lines.append(f"  - still failing: {m['check']} [{m['role']}] {m['n_failing']} items: "
                         + ", ".join(m["failing"]))
            lines += [f"    - {e}" for e in m["errors"]]
        if u["open_requirements"]:
            lines.append("  - open requirements: " + ", ".join(u["open_requirements"]))
    if data["regression"]:
        lines.append("Non-regression checks the harness runs on every following step (items that passed before):")
        lines += [f"- {c['id']} [{c['kind']}]: {c['items']} items" for c in data["regression"]]
    if data["retired"]:
        lines.append("Retired by a specification change: " + ", ".join(r["ref"] for r in data["retired"]))
    return "\n".join(lines), data


class Chain:
    """単位の列を順に回す積み重ねの周回（設計 §5-1）。単位ごとに run(unit, budget_s) を呼ぶ（単位の間のベンチ固有の手順は、
    呼ぶ側が挟む）。
    - 合格した単位は確定し（アダプタの checkpoint）、通った項目を非退行の検査として次の単位から積み、守る（guard と protect）
    - 落ちた単位（fail・infra）は、rollback なら単位の始まりに戻す（halt_on_stop で止めた単位は、人が discard を選んだときに戻す）
    - 合格せずに止まった単位（UNMET_STOPS）は、halt_on_stop なら列を止めて人の裁定（resume）を待ち、そうでなければ要約を
      残して次へ進む（ベンチの走り）
    - 単位の間の文脈は context（CONTEXTS）
    - protect を切ると、積んだ項目と固めた検査の元のファイルを守らない（項目の合否は見続ける）。後の単位が前の単位のテストを
      仕様として書き換えるベンチで、改廃（supersede）の宣言の出どころ（上流の台帳）が無い間の比較条件"""

    def __init__(self, port, adapter, *, expected_model, context="continue", gate=True, show_ledger=False,
                 max_rounds=4, stop_rules=None, second_gate=None, rollback=False, halt_on_stop=True,
                 infra_retries=1, protect=True, clock=time.monotonic):
        if context not in CONTEXTS:
            raise ValueError(f"context が違う: {context!r}（{CONTEXTS}）")
        self.port, self.adapter, self.second_gate = port, adapter, second_gate
        self.context, self.rollback, self.halt_on_stop, self.protect = context, rollback, halt_on_stop, protect
        self.opts = {"expected_model": expected_model, "gate": gate, "show_ledger": show_ledger,
                     "max_rounds": max_rounds, "stop_rules": stop_rules, "infra_retries": infra_retries,
                     "protect": protect, "clock": clock}
        self.regression, self.retired, self.records = [], [], []
        self.halted = None

    def _protect(self):
        if not self.protect or not getattr(self.adapter, "protects", False) or not self.regression:
            return []
        return [Check(PROTECT_ID, "protect", [c.id for c in self.regression], role=PROTECT)]

    def run(self, unit, budget_s):
        if self.halted is not None:
            raise RuntimeError("合格せずに止まった単位がある。resume で人の裁定を渡してから進める")
        k, uid = len(self.records) + 1, unit.ledger.unit
        retired = supersede(self.regression, unit.supersedes, uid)
        self.retired += retired
        if self.rollback:
            _optional(self.adapter, "checkpoint", f"{uid}:start")
        opening, prompt, handoff = "start", unit.prompt, None
        if k > 1 and self.context == "continue":
            opening = "send"
        elif k > 1 and self.context == "handoff":
            text, data = render_handoff(self.records, self.regression, self.retired)
            _optional(self.adapter, "write_handoff", text, data)
            prompt = unit.prompt + "\n\n" + text
            handoff = {"chars": len(text), "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        regression_in = [c.id for c in self.regression]
        rec = run_unit(self.port, self.adapter, unit.ledger, prompt, budget_s=budget_s, opening=opening,
                       checks=list(unit.ledger.checks) + self.regression + self._protect(),
                       second_gate=self.second_gate, **self.opts)
        rec["chain"] = {"k": k, "context": self.context, "opening": opening, "retired": retired, "handoff": handoff,
                        "regression_in": regression_in}
        self._settle(unit, rec)
        return rec

    def _settle(self, unit, rec):
        uid, ch = unit.ledger.unit, rec["chain"]
        if rec["decision"] == "pass":
            seen = _rounds_results(rec["rounds"])
            checks = list(unit.ledger.checks) + [Check(**d) for d in rec["second_checks"]]
            added = confirm(self.regression, checks, list(seen[-1][2].values()), uid)
            _optional(self.adapter, "checkpoint", f"{uid}:confirmed")
            if added and self.protect and getattr(self.adapter, "protects", False):
                _optional(self.adapter, "guard", added)
            ch.update(confirmed=True, regression_added=[c.id for c in added])
        else:
            ch["confirmed"] = False
            halt = self.halt_on_stop and rec["stop"] in UNMET_STOPS
            if self.rollback and rec["decision"] in ("fail", "infra") and not halt:
                _optional(self.adapter, "restore", f"{uid}:start")
                ch["rolled_back"] = True
            if halt:
                self.halted = {"unit": unit, "record": rec}
        ch["regression_out"] = [c.id for c in self.regression]
        self.records.append(rec)

    def resume(self, choice, budget_s, *, note="", supersedes=(), drop=()):
        """止まった単位に人の裁定を渡す。discard：単位を捨てる（rollback なら始まりに戻す）。fix_and_resume：同じ会話に人の文
        （note）を送り、同じ単位を同じ検査（固めた第 2 関門を含む）で続ける。change_spec：非退行の検査を改廃し（supersedes）、
        第 2 関門の検査を外して（drop）から続ける。続けた単位の記録を返す（discard は None）。"""
        if self.halted is None:
            raise RuntimeError("人の裁定を待つ単位が無い")
        if choice not in CHOICES:
            raise ValueError(f"選べるのは {CHOICES}: {choice!r}")
        unit, prev = self.halted["unit"], self.halted["record"]
        uid = unit.ledger.unit
        self.halted = None
        entry = {"choice": choice, "after": prev["stop"], "note_chars": len(note)}
        if choice == "discard":
            if self.rollback:
                _optional(self.adapter, "restore", f"{uid}:start")
                entry["rolled_back"] = True
            prev["chain"]["resume"] = entry
            return None
        retired = supersede(self.regression, supersedes, uid) if choice == "change_spec" else []
        self.retired += retired
        dropped = set(drop or ())
        second = [Check(**d) for d in prev["second_checks"] if d["id"] not in dropped]
        entry.update(retired=retired, dropped=sorted(dropped))
        regression_in = [c.id for c in self.regression]
        rec = run_unit(self.port, self.adapter, unit.ledger, note, budget_s=budget_s, opening="send",
                       checks=list(unit.ledger.checks) + self.regression + self._protect(),
                       second_gate=self.second_gate, second_checks=second if prev["second_checks"] else None,
                       **self.opts)
        rec["chain"] = {"k": len(self.records) + 1, "context": self.context, "opening": "send", "retired": retired,
                        "handoff": None, "regression_in": regression_in, "resume": entry}
        self._settle(unit, rec)
        return rec


def run_chain(port, adapter, units, budget_s, **kw):
    """単位の列を順に回す（単位の間にベンチ固有の手順が無いとき）。人の裁定を待つ単位が出たら止める。Chain を返す。"""
    chain = Chain(port, adapter, **kw)
    for unit in units:
        chain.run(unit, budget_s)
        if chain.halted is not None:
            break
    return chain


# ---------------------------------------------------------------- 記録

def total_usage(record):
    """記録のターンの利用量を足し合わせる（各呼び出しの利用量はターンの足し込み。会話の累計は足さない）。"""
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
