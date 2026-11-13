"""FeatureBench の fb infer に、agy のエージェントを足す（実験と計画の 0-b。FeatureBench 0.3.0、コミット 08b25d9 に合わせる）。

    python -m harness.fb_agy infer <fb infer の引数…> --agent agy --model <モデル>
    python -m harness.fb_agy check <ランの置き場> --token-file <トークン>   回収したパッチとログへの漏れを確かめる

FeatureBench のコードは書き換えない。起動のときに次の 3 つだけを包む（register）。
1. fb infer の --agent の選び（argparse の choices）に agy を足す
2. エージェントの表（get_agent）に AgyAgent を足す
3. 中継の宛先の決め方（resolve_model_api_endpoints）を、agy では AGY_HOSTS の 3 つ（箱の中継の許しの一覧と同じ）にする

agy の入れ方・認証・呼び方：
- 入れる段（公開の網が使える）で、config/box.json の URL から agy を取り、SHA-512 を照らして /usr/local/bin に置く
- 認証のトークンは、コンテナの HOME の下（TOKEN_DIR）に、ログに中身を残さない形（put_archive）で置く。/testbed の外
- 頼み（課題の文だけ）は PROMPT_PATH から標準入力で渡し、stream-json の出力は STREAM_PATH にも残す。どちらも /testbed の外
- 走りの後に、回収したパッチ（/testbed の git の差分）と infer.log に、トークンの文字列と .gemini のパスが無いことを確かめる（check）

環境変数：FB_AGY_TOKEN（WSL の上のトークンのファイル。既定は config/box.json の token）、FB_AGY_FAKE=1（偽物の agy を入れる。利用枠 0）。
"""
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agy_stream  # noqa: E402
import exitcode  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOX_CONFIG = ROOT / "config" / "box.json"
AGENT_CONFIG = ROOT / "config" / "fb_agy.json"
NAME = "agy"
AGY_HOSTS = ("daily-cloudcode-pa.googleapis.com", "oauth2.googleapis.com", "www.googleapis.com")
HOME = "/root"
TOKEN_DIR = HOME + "/.gemini/antigravity-cli"
TOKEN_NAME = "antigravity-oauth-token"   # agy が読む名前（元のファイルの名前に依らない）
LOG_DIR = "/agent-logs"
PROMPT_PATH = LOG_DIR + "/agy_prompt.jsonl"
STREAM_PATH = LOG_DIR + "/agy_stream.jsonl"
DEFAULT_TTL = 3600

FAKE_AGY = r'''#!/usr/bin/env python3
"""偽物の agy（0-b の確かめ用。利用枠を使わない）。本物と同じ置き場に会話の記録を書き、作業の木に 1 つだけファイルを作る。"""
import json, os, pathlib, sys
data = sys.stdin.read()
lines = [line for line in data.splitlines() if line.strip()]
prompt = json.loads(lines[0])["message"]["content"] if lines else ""
args = sys.argv[1:]
model = args[args.index("--model") + 1] if "--model" in args else "?"
home = pathlib.Path(os.environ.get("HOME", "/root"))
token = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
brain = home / ".gemini" / "antigravity-cli" / "brain" / "fake-conv"
brain.mkdir(parents=True, exist_ok=True)
(brain / "transcript.md").write_text("fake conversation record\n")
pathlib.Path("FAKE_AGY_CHANGE.txt").write_text("written by the fake agy\n")
def reach(url):
    import urllib.error, urllib.request
    try:
        urllib.request.urlopen(url, timeout=8)
        return "open"
    except urllib.error.HTTPError as e:
        return "open(%d)" % e.code
    except Exception as e:
        return "blocked(%s)" % type(e).__name__
net = " ".join("%s=%s" % (h, reach("https://%s/" % h)) for h in ("oauth2.googleapis.com", "pypi.org", "github.com"))
def ev(name, body):
    print(json.dumps({"event": name, name: body}), flush=True)
ev("init", {"model": model, "conversation_id": "fake-conv"})
ev("step_update", {"step_index": 0, "step_type": "agent_response", "state": "DONE",
                   "usage": {"input_tokens": 10, "output_tokens": 2, "thinking_tokens": 0, "cache_read_tokens": 0}})
ev("result", {"status": "SUCCESS", "conversation_id": "fake-conv",
              "response": "fake done: prompt %d chars, token %s, net %s" % (len(prompt), "present" if token.is_file() else "missing", net),
              "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}})
'''


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def install_script(box_cfg, fake=False):
    """入れる段のスクリプト。本物は配布の URL から取り、SHA-512 を照らす（箱の像と同じ手順）。"""
    if fake:
        return "set -eu\ncat > /usr/local/bin/agy << 'FAKEEOF'\n" + FAKE_AGY + "FAKEEOF\nchmod 755 /usr/local/bin/agy\n"
    agy = box_cfg["agy"]
    return "\n".join([
        "set -eu",
        "if ! command -v curl >/dev/null 2>&1; then apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null; fi",
        f"curl -fsSL -o /tmp/agy.tar.gz {shlex.quote(agy['url'])}",
        f"echo {shlex.quote(agy['sha512'] + '  /tmp/agy.tar.gz')} | sha512sum -c -",
        "tar -xzf /tmp/agy.tar.gz -C /usr/local/bin antigravity",
        "mv /usr/local/bin/antigravity /usr/local/bin/agy",
        "chmod 755 /usr/local/bin/agy",
        "rm -f /tmp/agy.tar.gz",
        "",
    ])


def agent_settings(agent_cfg, model):
    """config/fb_agy.json の旗に、fb infer の --model（正確な ID）を入れたもの。"""
    if not model:
        raise ValueError("fb infer の --model（正確なモデルの ID）が要る")
    return dict(agent_cfg, model_name=model)


def run_command(imp, ttl):
    """/testbed で走らせる命令。頼みは PROMPT_PATH から標準入力で渡し、出力は STREAM_PATH にも残す。自動更新は止める（版の固定。箱の像と同じ）。"""
    argv = agy_stream.args(imp, [NAME], "", ttl)
    return (f"set -o pipefail; mkdir -p {LOG_DIR}; HOME={HOME} AGY_CLI_DISABLE_AUTO_UPDATE=1 {shlex.join(argv)} < {PROMPT_PATH} "
            f"| tee {STREAM_PATH}")


def leak_problems(patch, log_text, token):
    """回収したパッチとログに、トークンの文字列と .gemini のパスが無いか。[問題の文]。"""
    found = []
    secret = (token or "").strip()
    if secret:
        if secret in (patch or ""):
            found.append("パッチにトークンの文字列がある")
        if secret in (log_text or ""):
            found.append("ログにトークンの文字列がある")
    for line in (patch or "").splitlines():
        if line.startswith("diff --git") and (".gemini/" in line or "antigravity" in line):
            found.append(f"パッチに agy の置き場のファイルがある：{line[11:].strip()}")
    return found


def check_run(run_dir, token):
    """ランの置き場（runs/<時刻>）の output.jsonl のパッチと、run_outputs の下のログを確かめる。[問題の文]。"""
    run_dir = Path(run_dir)
    found = []
    for line in (run_dir / "output.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        patch = rec.get("model_patch") or ""
        found += [f"{rec.get('instance_id')}: {p}" for p in leak_problems(patch, "", token)]
    for log in sorted(run_dir.rglob("*.log")) + sorted(run_dir.rglob("*.jsonl")):
        found += [f"{log.relative_to(run_dir)}: {p}" for p in leak_problems("", log.read_text(encoding="utf-8", errors="replace"), token)]
    return found


def register():
    """FeatureBench に agy を足す（WSL の FeatureBench の venv の中でだけ呼ぶ）。"""
    import argparse

    import featurebench.infer.agents as agents_mod
    import featurebench.infer.network as network
    import featurebench.infer.run_infer as run_infer
    from featurebench.infer.agents.base import BaseAgent

    box_cfg = load_json(BOX_CONFIG)
    agent_cfg = load_json(AGENT_CONFIG)
    fake = os.environ.get("FB_AGY_FAKE") == "1"
    token_file = Path(os.environ.get("FB_AGY_TOKEN") or box_cfg["token"])

    class AgyAgent(BaseAgent):
        def __init__(self, container_manager, env_vars=None, logger=None, model=None, **kwargs):
            super().__init__(container_manager, env_vars=env_vars, logger=logger, **kwargs)
            self.imp = agent_settings(agent_cfg, model)
            self.ttl = DEFAULT_TTL

        @property
        def name(self):
            return NAME

        @property
        def install_script(self):
            return install_script(box_cfg, fake=fake)

        def run(self, container, instruction, log_file, timeout=None):
            self.ttl = int(timeout or DEFAULT_TTL)
            return super().run(container, instruction, log_file, timeout=timeout)

        def pre_run_hook(self, container, log_file):
            self.cm.exec_command(container, f"mkdir -p {LOG_DIR}", log_file=log_file)
            return True

        def prepare_run(self, container, instruction, log_file):
            with tempfile.TemporaryDirectory() as d:
                prompt = Path(d) / "prompt.jsonl"
                prompt.write_text(agy_stream.stdin_for(self.imp, instruction), encoding="utf-8")
                self.cm.copy_to_container(container, prompt, PROMPT_PATH)
                token = Path(d) / TOKEN_NAME
                token.write_bytes(token_file.read_bytes())
                os.chmod(token, 0o600)
                self.cm.copy_to_container(container, token, f"{TOKEN_DIR}/{TOKEN_NAME}")
            return True

        def get_run_command(self, instruction):
            return run_command(self.imp, self.ttl)

        def post_run_hook(self, container, log_file):
            log_dir = Path(log_file).parent
            local = log_dir / "agy_stream.jsonl"
            self.cm.copy_from_container(container, STREAM_PATH, local)
            parsed = agy_stream.parse(local.read_text(encoding="utf-8", errors="replace") if local.exists() else "")
            reported = parsed.get("model")
            (log_dir / "agy_usage.json").write_text(json.dumps({
                "requested_model": self.imp["model_name"], "reported_model": reported, "usage": parsed.get("usage"),
                "outcome": parsed.get("outcome"), "complete": parsed.get("complete")}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            if reported != self.imp["model_name"]:
                self.agent_exit_status = f"model_mismatch: {reported}"
                return False
            return True

    original_get_agent = agents_mod.get_agent

    def get_agent(agent_name, **kwargs):
        if agent_name == NAME:
            return AgyAgent(**kwargs)
        return original_get_agent(agent_name, **kwargs)

    agents_mod.get_agent = get_agent
    run_infer.get_agent = get_agent

    original_resolve = network.resolve_model_api_endpoints

    def resolve_model_api_endpoints(agent, model, env_vars):
        if agent == NAME:
            return tuple(sorted(network.ApiEndpoint(host, 443) for host in AGY_HOSTS))
        return original_resolve(agent, model, env_vars)

    network.resolve_model_api_endpoints = resolve_model_api_endpoints

    original_add_argument = argparse.ArgumentParser.add_argument

    def add_argument(self, *names, **kwargs):
        if "--agent" in names and kwargs.get("choices"):
            kwargs = dict(kwargs, choices=[*kwargs["choices"], NAME])
        return original_add_argument(self, *names, **kwargs)

    argparse.ArgumentParser.add_argument = add_argument
    return run_infer


def infer_args(args):
    """fb infer に渡す引数。コミット 08b25d9 の fb infer は、--force-rerun が無いと、空の一覧を「空でない一覧」の検査に通して
    必ず止まる（models.py の _validate_ids）。--task-id があって --force-rerun が無ければ、同じ ID を --force-rerun にも渡す（新しいランでは害が無い）。"""
    args = list(args)
    if "--force-rerun" in args or "--task-id" not in args:
        return args
    i = args.index("--task-id") + 1
    ids = []
    while i < len(args) and not args[i].startswith("-"):
        ids.append(args[i])
        i += 1
    return args + (["--force-rerun", *ids] if ids else [])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["infer"]:
        run_infer = register()
        sys.argv = ["fb infer", *infer_args(argv[1:])]
        return run_infer.main()
    if argv[:1] == ["check"] and len(argv) >= 2:
        token_file = argv[argv.index("--token-file") + 1] if "--token-file" in argv else load_json(BOX_CONFIG)["token"]
        token = Path(token_file).read_text(encoding="utf-8", errors="replace")
        found = check_run(argv[1], token)
        for f in found:
            print(f"NG: {f}")
        print("合格" if not found else f"不合格（{len(found)} 件）")
        return 0 if not found else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(exitcode.normalized(main))
