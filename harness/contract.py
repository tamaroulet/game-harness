"""契約（ゲームのリポジトリ直下の .harness.toml）の最小の読み込み。

    読むのは [static] の forbidden（{pattern, label} の表の配列）と selftest_forbidden_probe だけ。
    docs/design/contract.md §6.7 の全面実装ではない（docs/design/spec_pipeline.md §11.2、S14）。
    ほかの表・キーは、ここでは読まない（検証もしない）。

**なぜ作業ツリーではなく git のオブジェクトから読むのか**（contract.md §4.1）
契約は、保護された既定ブランチの先頭からだけ読む。サンドボックスや作業ツリーの .harness.toml は、
実装役やエージェントが書き換えうる。`git show <sha>:.harness.toml` なら、作業ツリーの状態に影響されない。

**読めなければ止まる**（contract.md §1-3）
ファイルが無い・TOML として読めない・型が違う・正規表現が壊れている・probe がどのパターンにも当たらない、
のいずれかなら ContractError。既定値に落として続行しない。
"""
import re
import tomllib

from proc import run

PATH = ".harness.toml"
FORBIDDEN_KEYS = {"pattern", "label"}


class ContractError(Exception):
    pass


def read_from_base(repo, base, ttl):
    """origin/<base> の先頭を取ってきて、その SHA の .harness.toml を返す。(sha, 本文)"""
    rc, out, err = run(["git", "fetch", "origin", base], repo, ttl, "git fetch (contract)")
    if rc != 0:
        raise ContractError(f"origin/{base} を取得できません: {(err or out)[:200]}")
    rc, out, err = run(["git", "rev-parse", f"origin/{base}"], repo, ttl, "git rev-parse (contract)")
    if rc != 0:
        raise ContractError(f"origin/{base} の SHA を読めません: {(err or out)[:200]}")
    sha = out.strip()
    rc, out, err = run(["git", "show", f"{sha}:{PATH}"], repo, ttl, "git show (contract)")
    if rc != 0:
        raise ContractError(f"{base}（{sha[:8]}）に {PATH} がありません: {(err or out)[:200]}")
    return sha, out


def parse_static(text):
    """[static] の禁止パターンと probe。{"forbidden": [(pattern, label), ...], "probe": str | None}"""
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ContractError(f"{PATH} が TOML として読めません: {e}")
    static = d.get("static", {})
    if not isinstance(static, dict):
        raise ContractError("[static] が表ではありません")

    items = static.get("forbidden", [])
    if not isinstance(items, list):
        raise ContractError("[[static.forbidden]] が表の配列ではありません")
    forbidden = []
    for i, item in enumerate(items):
        where = f"[[static.forbidden]] の {i + 1} 件目"
        if not isinstance(item, dict):
            raise ContractError(f"{where} が表ではありません")
        extra = set(item) - FORBIDDEN_KEYS
        if extra:
            # applies_to などは未対応。黙って無視すると、書いた人の意図より緩く効く
            raise ContractError(f"{where} に未対応のキーがあります: {', '.join(sorted(extra))}")
        for key in sorted(FORBIDDEN_KEYS):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ContractError(f"{where} の {key} が空でない文字列ではありません")
        try:
            re.compile(item["pattern"])
        except re.error as e:
            raise ContractError(f"{where} の pattern が正規表現として読めません: {e}")
        forbidden.append((item["pattern"], item["label"]))

    probe = static.get("selftest_forbidden_probe")
    if forbidden:
        if not isinstance(probe, str) or not probe:
            raise ContractError("[[static.forbidden]] があるのに static.selftest_forbidden_probe がありません")
        if not any(re.search(p, probe) for p, _ in forbidden):
            # 自己検査が空振りする（既存の教訓）
            raise ContractError("static.selftest_forbidden_probe がどの禁止パターンにも当たりません")
    elif probe is not None and not isinstance(probe, str):
        raise ContractError("static.selftest_forbidden_probe が文字列ではありません")
    return {"forbidden": forbidden, "probe": probe}


def load(repo, base, ttl):
    """{"sha", "forbidden", "probe"}。1 回の実行で 1 回だけ呼ぶ（途中で main が進んでも読み直さない）。"""
    sha, text = read_from_base(repo, base, ttl)
    return {"sha": sha, **parse_static(text)}


def effective_forbidden(contract, unit):
    """契約 ∪ 単位定義。単位定義（分解役の出力）は足せるが、契約のパターンは消せない。"""
    if contract is None:
        raise ContractError("契約が読み込まれていません")
    seen, out = set(), []
    for pattern, label in list(contract["forbidden"]) + [tuple(x) for x in unit.get("forbidden_patterns", [])]:
        if pattern not in seen:
            seen.add(pattern)
            out.append((pattern, label))
    return out
