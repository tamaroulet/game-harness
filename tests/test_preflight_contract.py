"""実行口の設定の契約の試験（副操縦士のプリフライトの検査 3 つをリポジトリの試験に移したもの。docker と WSL に依らない）。

    python -m unittest tests.test_preflight_contract -v

- WSL のメモリの上限：ベンチ固有の設定（config/*_run.json）のコンテナのメモリの上限は、WSL のメモリより小さい（この試験）
- uid 0 の実装役のフック：走りの後の照合が hooks.json だけでなくスクリプトの実体も見る（tests/test_agy_port.py の
  test_hook_scripts_are_part_of_the_post_run_check。uid 0 の設定があるときの守りはこの照合だけなので、この試験で結びを見る）
- collect の終わりの符号：VibePrevisBench の collect の落ちを 0 点にしない（tests/test_vpb_run.py の
  test_collect_failure_is_infra_not_zero）
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_port  # noqa: E402

# WSL のメモリ（2026-11-26 に .wslconfig を memory=22GB にした後の free の Mem）。WSL の設定を変えたら直す
WSL_MEM_MIB = 22015
MEM_KEYS = ("memory", "memory_mb", "mem_cap_mb")


def mib(value, key):
    """設定の値 → MiB（"12g"・"512m"・数。memory_mb と mem_cap_mb は MiB の数）。"""
    if isinstance(value, (int, float)):
        return int(value)
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([gGmM]?)\s*", str(value))
    if not m:
        raise ValueError(f"{key} の値が読めない: {value!r}")
    n = float(m.group(1))
    return int(n * 1024) if m.group(2).lower() == "g" else int(n)


def run_configs():
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "config").glob("*_run.json"))}


class PreflightContractTests(unittest.TestCase):
    def test_container_memory_caps_stay_below_wsl_memory(self):
        caps = {}
        for name, cfg in run_configs().items():
            for k in MEM_KEYS:
                if k in cfg:
                    caps[f"{name}:{k}"] = mib(cfg[k], k)
        self.assertIn("ms_run.json:mem_cap_mb", caps, "Multi-step SWE の上限が設定にある")
        for where, v in caps.items():
            self.assertLess(v, WSL_MEM_MIB, f"{where} が WSL のメモリ以上（ホストごと落ちる恐れ）")

    def test_root_agents_check_the_hook_script_bodies(self):
        root_cfgs = [n for n, c in run_configs().items() if (c.get("sandbox") or {}).get("uid") == 0]
        if not root_cfgs:
            self.skipTest("uid 0 の実装役の設定が無い")
        names = set(agy_port.hook_files({"home": "/root", "uid": 0}))
        for script in agy_port.HOOK_SCRIPTS:
            self.assertIn(f"{agy_port.HOOKS_DIR}/{script}", names, f"{root_cfgs} の走りの後の照合にスクリプトの実体が入る")


if __name__ == "__main__":
    unittest.main()
