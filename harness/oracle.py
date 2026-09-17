"""二相判定（F2P / P2P）と、その判定に使うオラクル設定の検証。

テスト結果は {テスト名: 結果} の辞書で受け取る。結果の語（Passed など）はアダプタ A が持つ
（A.PASSED / A.FAILED / A.SKIPPED。SKIPPED の無いアダプタは空とみなす）。
ここはエンジンにも言語にも依存しない。子プロセスも起動しない（判定だけ）。

**なぜ件数ではなく名前で見るか**

件数の合算は入れ替わりを見逃す。base で {A: Failed, B: Passed} だったものが
実装後に {A: Passed, B: Failed} になると、Passed の件数は変わらないのに B が壊れている。
逆に、base で既に通っている「偽テスト」も、件数が増えていれば紛れ込む。
したがって、名前ごとに状態遷移を確かめる。

  F2P（Fail-to-Pass） : 受入テストの各件が base で Failed、実装後に Passed
                        base がビルドできない（実装がまだ無い）ときは「未ビルド」として
                        非 Passed を認める。その場合のビルド可能性は呼び出し側が別に確かめる
  P2P（Pass-to-Pass） : base で Passed だった全件が、実装後も Passed（消えたものも破壊とみなす）
                        除外できるのは quarantine（承認した PR 番号つき）だけ

設定（docs/design/contract.md §3.5 と同じ形）:
  control_must_pass / control_must_fail   いまは pipeline.json の control_groups から渡す
                                          （unity-2d の採取スクリプトが同じキーを読むため）
  quarantine = [{test, reason, approved_in}]   pipeline.json の oracle.quarantine
"""

ORACLE_KEYS = {"quarantine"}
QUARANTINE_KEYS = {"test", "reason", "approved_in"}


class OracleError(Exception):
    pass


# ============================================================ 設定の検証

def load(control_groups, table):
    """制御群と quarantine を検証して返す。読めなければ例外（既定値に落とさない）。"""
    if not isinstance(control_groups, dict):
        raise OracleError("control_groups は {must_pass, must_fail} の表で書いてください")
    must_pass = control_groups.get("must_pass")
    must_fail = control_groups.get("must_fail")
    for key, v in (("must_pass", must_pass), ("must_fail", must_fail)):
        if not (isinstance(v, str) and v.strip()):
            raise OracleError(f"control_groups.{key} がありません（空文字も不可）")

    if table is None:
        table = {}
    if not isinstance(table, dict):
        raise OracleError("oracle は表で書いてください")
    # 制御群を oracle 側にも書けると、2 か所に書いて片方だけ直す事故になる。知らないキーは止める。
    unknown = sorted(set(table) - ORACLE_KEYS)
    if unknown:
        raise OracleError("oracle に知らないキーがあります: " + ", ".join(unknown))

    entries = table.get("quarantine", [])
    if not isinstance(entries, list):
        raise OracleError("oracle.quarantine はリストで書いてください")

    names = []
    for i, e in enumerate(entries):
        where = f"oracle.quarantine[{i}]"
        if not isinstance(e, dict):
            raise OracleError(f"{where} は {{test, reason, approved_in}} の表で書いてください")
        extra = sorted(set(e) - QUARANTINE_KEYS)
        if extra:
            raise OracleError(f"{where} に知らないキーがあります: " + ", ".join(extra))
        for key in ("test", "reason"):
            v = e.get(key)
            if not (isinstance(v, str) and v.strip()):
                raise OracleError(f"{where}.{key} がありません（空文字も不可）")
        pr = e.get("approved_in")
        if not (isinstance(pr, int) and not isinstance(pr, bool) and pr > 0):
            raise OracleError(f"{where}.approved_in が承認した PR 番号（正の整数）ではありません: {pr!r}。"
                              "承認の無い隔離は P2P から外せません")
        t = e["test"]
        if t in names:
            raise OracleError(f"{where}.test が重複しています: {t}")
        if t in (must_pass, must_fail):
            raise OracleError(f"{where}.test に制御群 {t} は入れられません")
        names.append(t)

    return {"must_pass": must_pass, "must_fail": must_fail, "quarantine": tuple(names)}


# ============================================================ 名前の照合

def hits(results, needle):
    """部分一致。受入テストや制御群はクラス名・ケース名の一部で指定されるため。"""
    return [n for n in results if n and needle in n]


def is_quarantined(name, quarantine):
    """完全一致か、「.」区切りの末尾一致だけ。部分一致にすると "Tests" で全件が外れる。"""
    return any(name == t or name.endswith("." + t) for t in quarantine)


def _skipped(A):
    return tuple(getattr(A, "SKIPPED", ()))


# ============================================================ 制御群

def controls(results, A, must_pass, must_fail, require_fail):
    """制御群の検査。戻り値は ABORT の理由のリスト（空なら正常）。"""
    aborts = []
    ok_hits = hits(results, must_pass)
    if not ok_hits:
        aborts.append(f"必ず通る対照群 {must_pass} が実行されていない")
    else:
        bad = [n for n in ok_hits if results[n] != A.PASSED]
        if bad:
            aborts.append(f"必ず通る対照群が通らなかった: {bad[0]}={results[bad[0]]}")

    ng_hits = hits(results, must_fail)
    if require_fail and not ng_hits:
        aborts.append(f"必ず落ちる対照群 {must_fail} が実行されていない")
    wrong = [n for n in ng_hits if results[n] != A.FAILED]
    if wrong:
        aborts.append(f"必ず落ちる対照群が落ちなかった（環境異常かモックの偽装）: "
                      f"{wrong[0]}={results[wrong[0]]}")
    return aborts


# ============================================================ F2P

def f2p_base(required, suites):
    """実装前の検査。suites = [(A, base)]。base が None のスイートは未ビルド。

    戻り値 (fake, aborts)。fake は base で既に Passed だった受入テスト（偽テスト）。
    """
    fake, aborts = [], []
    all_built = all(base is not None for _, base in suites)
    for name in required:
        found = False
        for A, base in suites:
            if base is None:
                continue
            for n in hits(base, name):
                found = True
                o = base[n]
                if o == A.PASSED:
                    fake.append(n)
                elif o != A.FAILED:
                    aborts.append(f"受入テスト {n} が base で {o}（Failed であるべき）")
        if not found and all_built:
            aborts.append(f"受入テスト {name} が base で 1 件も実行されていない")
    return fake, aborts


def f2p_impl(required, suites):
    """実装後の検査。suites = [(A, base, impl)]。base が None なら名前の追跡はしない。

    戻り値 (ng, aborts)。ng は通っていない受入テスト（実装の不備）。
    quarantine はここでは使わない（受入テストを隔離で外させない）。
    """
    ng, aborts = [], []
    for name in required:
        found = False
        for A, base, impl in suites:
            for n in hits(impl, name):
                found = True
                o = impl[n]
                if o in _skipped(A):
                    aborts.append(f"受入テスト {n} が skip されている")
                    continue
                if base is not None and n not in base:
                    aborts.append(f"受入テスト {n} が base に無い（名前を追跡できない）")
                    continue
                if o != A.PASSED:
                    ng.append(n)
        if not found:
            aborts.append(f"受入テスト {name} が 1 件も実行されていない")
    return ng, aborts


# ============================================================ P2P

def p2p(base, impl, A, quarantine):
    """base で Passed だった全件のうち、実装後に Passed でないもの（消えたものを含む）。"""
    kept = [n for n, o in base.items() if o == A.PASSED and not is_quarantined(n, quarantine)]
    return sorted(n for n in kept if impl.get(n) != A.PASSED)


def p2p_count(base, A, quarantine):
    return sum(1 for n, o in base.items() if o == A.PASSED and not is_quarantined(n, quarantine))


def unexpected_failures(results, A, must_fail, quarantine, also_exclude=()):
    """Failed のうち、必ず落ちる対照群・隔離・also_exclude（部分一致）のどれでもないもの。"""
    return sorted(n for n, o in results.items()
                  if o == A.FAILED
                  and must_fail not in (n or "")
                  and not is_quarantined(n, quarantine)
                  and not any(x in (n or "") for x in also_exclude))
