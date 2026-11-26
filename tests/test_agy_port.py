"""実装役 agy の実行口（harness/agy_port.py）の、docker に依らない部品の試験。

    python -m unittest discover -s tests -v
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import agy_port  # noqa: E402
import agy_stream  # noqa: E402
import sandbox  # noqa: E402


def step(i, tool, state, error=None):
    """agy の stream-json のステップ（道具のエラーは state: ERROR と tool_info.error。実際の agy の記録の形）。"""
    su = {"step_index": i, "step_type": "tool", "tool_name": tool, "state": state}
    if error:
        su["tool_info"] = {"name": tool, "error": {"type": "TOOL_ERROR", "message": error}}
    return {"event": "step_update", "step_update": su}


# 2026-11-22 の deny の確認（本物の agy 1.5.3・gemini-4-hydrogen-medium）の stream-json の実際のイベント。会話の ID だけ置き換え、
# init・user_input・agent_response のステップは略した。search_web は tool_info.error に deny の文が入り、invoke_subagent は
# ERROR のイベントで step_type が subagent に変わり、subagent_info.subagents が空で、エラーの文が無い（文は返事の引用にだけ出る）
MEASURED_REPLY = (
    "- `search_web`: returned an error — `\"Encountered error in tool execution: tool call denied by pre-tool hook: "
    "Web access is not available in this environment.\"`\n"
    "- `read_url_content`: not called — tool is not declared/available in this environment's toolset.\n"
    "- `read_browser_page`: not called — tool is not declared/available in this environment's toolset.\n"
    "- `invoke_subagent`: returned an error — `\"Encountered error in tool execution: tool call denied by pre-tool hook: "
    "Subagents are not available in this environment.\"`\n")
MEASURED_DENY_CHECK = [
    {"event": "step_update", "step_update": {
        "conversation_id": "c", "step_index": 2, "state": "ACTIVE", "step_type": "tool", "tool_name": "search_web",
        "tool_info": {"name": "search_web", "parameters": {"query": "SlopCodeBench"}}}},
    {"event": "step_update", "step_update": {
        "conversation_id": "c", "step_index": 2, "state": "ERROR", "step_type": "tool", "tool_name": "search_web",
        "duration_seconds": 0.279626928,
        "tool_info": {"name": "search_web", "parameters": {"query": "SlopCodeBench"}, "error": {
            "type": "TOOL_ERROR",
            "message": "tool call denied by pre-tool hook: Web access is not available in this environment."}}}},
    {"event": "step_update", "step_update": {
        "conversation_id": "c", "step_index": 4, "state": "ACTIVE", "step_type": "tool", "tool_name": "invoke_subagent",
        "tool_info": {"name": "invoke_subagent", "parameters": {"Role": "Permission Check Subagent", "TypeName": "self"}}}},
    {"event": "step_update", "step_update": {
        "conversation_id": "c", "step_index": 4, "state": "ERROR", "step_type": "subagent", "tool_name": "invoke_subagent",
        "duration_seconds": 0.298547666, "subagent_info": {"subagents": []}}},
    {"event": "result", "result": {"conversation_id": "c", "status": "SUCCESS", "response": MEASURED_REPLY,
                                   "duration_seconds": 11.492606876, "num_turns": 1}},
]


class ConfigTests(unittest.TestCase):
    def test_config_pins_model_and_stream(self):
        agent = agy_port.load_config()["agent"]
        self.assertEqual(agent["model_name"], "gemini-4-hydrogen-medium")
        self.assertEqual(agent["model_flag"], "--model")
        self.assertIn("stream-json", agent["output_format_args"])
        self.assertEqual(agent["install"]["version"], "1.5.3")

    def test_hooks_deny_every_denied_tool(self):
        hooks = json.loads((agy_port.ASSETS / "hooks.json").read_text(encoding="utf-8"))
        entries = hooks["agy-deny-tools"]["PreToolUse"]
        script = {h["matcher"]: h["hooks"][0]["command"] for h in entries}
        self.assertEqual(set(script), set(agy_port.DENIED_TOOLS))
        self.assertTrue(all(c.startswith(agy_port.HOOKS_DIR + "/") for c in script.values()))
        name = {t: c.rsplit("/", 1)[-1] for t, c in script.items()}
        self.assertEqual({t for t, s in name.items() if s == "deny_web.sh"}, set(agy_port.WEB_TOOLS))
        self.assertEqual({t for t, s in name.items() if s == "deny_subagent.sh"}, set(agy_port.SUBAGENT_TOOLS))
        for s in agy_port.HOOK_SCRIPTS:
            self.assertIn('"decision": "deny"', (agy_port.ASSETS / s).read_text(encoding="utf-8"))
        self.assertEqual(len(entries), len(set(script)))   # 同じ道具を 2 度書かない

    # 2026-11-22 の Stage 1 の 1a で、agy 1.5.3（gemini-4-hydrogen-medium）の init が返した道具の一覧（60）
    OFFERED_1A = (
        "ask_custom_permission ask_permission ask_question browser_click_element browser_drag_pixel_to_pixel browser_get_dom "
        "browser_get_network_request browser_input browser_list_network_requests browser_mouse_down browser_mouse_up "
        "browser_move_mouse browser_press_key browser_refresh_page browser_resize_window browser_scroll browser_scroll_dom "
        "browser_select_option browser_subagent call_mcp_tool capture_browser_console_logs capture_browser_screenshot "
        "click_browser_pixel command_status define_subagent delete_knowledge execute_browser_javascript find_by_name finish "
        "generate_image grep_search invoke_subagent list_browser_pages list_dir list_permissions list_plugin_accounts "
        "list_resources manage_inbox manage_subagents manage_task multi_replace_file_content notebook_edit notebook_execution "
        "open_browser_url read_browser_page read_resource read_url_content replace_file_content run_command run_workflow "
        "schedule search_marketplace search_web sed_file send_command_input send_message view_file wait wait_5_seconds "
        "write_to_file").split()

    def test_offered_web_and_subagent_tools_are_denied(self):
        """一覧のうち、ブラウザ系・外から取る道具・別のモデルやエージェントに仕事を渡す口は、どれも止める表にある。"""
        offered = set(self.OFFERED_1A)
        self.assertEqual(len(offered), 60)
        web = {t for t in offered if "browser" in t} | {"search_web", "read_url_content", "search_marketplace"}
        self.assertEqual(len({t for t in offered if "browser" in t} - {"open_browser_url", "browser_subagent"}), 21)
        self.assertLessEqual(web, set(agy_port.WEB_TOOLS))
        self.assertLessEqual({"invoke_subagent", "define_subagent", "manage_subagents", "run_workflow"},
                             set(agy_port.SUBAGENT_TOOLS))
        self.assertLessEqual(set(agy_port.DENY_CHECK_TOOLS), set(agy_port.DENIED_TOOLS) & offered)


class BinaryPinTests(unittest.TestCase):
    """取った後の agy の実行ファイルを、呼ばれるたびに（走りのたびに）設定の SHA-512 と照らし、違えば止まる。"""

    @staticmethod
    def agent(cache, want):
        return {"cache": str(cache), "install": {"version": "1.5.3", "binary_sha512": want}}

    @staticmethod
    def cached(d, content):
        binary = Path(d) / "1.5.3" / "antigravity"
        binary.parent.mkdir(exist_ok=True)
        binary.write_bytes(content)
        return binary

    def test_config_pins_the_binary(self):
        inst = agy_port.load_config()["agent"]["install"]
        self.assertRegex(inst["binary_sha512"], r"^[0-9a-f]{128}$")
        self.assertNotEqual(inst["binary_sha512"], inst["sha512"])   # 配布物（tar.gz）の値と取り違えない

    def test_cached_binary_is_checked_on_every_call(self):
        with tempfile.TemporaryDirectory() as d:
            binary = self.cached(d, b"pinned agy")
            agent = self.agent(d, hashlib.sha512(b"pinned agy").hexdigest())
            self.assertEqual(agy_port.ensure_agy(agent), binary)
            self.cached(d, b"self-updated agy")   # 取った後に書き換わった
            with self.assertRaisesRegex(RuntimeError, "SHA-512"):
                agy_port.ensure_agy(agent)

    def test_missing_pin_stops(self):
        with tempfile.TemporaryDirectory() as d:
            self.cached(d, b"agy")
            with self.assertRaisesRegex(RuntimeError, "binary_sha512"):
                agy_port.ensure_agy(self.agent(d, None))


class DenyCheckTests(unittest.TestCase):
    """deny の確認の判定（実験と計画 §6-5）：頼んだ道具がどれも呼ばれ、どのステップもフックの deny で止まったときだけ合格。"""

    DENY = "Tool call denied by pre-tool hook: blocked by the harness"

    @staticmethod
    def stream(*events):
        return "\n".join(json.dumps(e) for e in events)

    def verdict(self, *events):
        return agy_port.deny_verdict(agy_port.tool_attempts(self.stream(*events), agy_port.DENY_CHECK_TOOLS))

    def denied_all(self, **override):
        """指示した道具をどれもフックの deny で返すステップ（override で 1 つの道具のステップを差し替える）。"""
        return [override.get(t, step(i, t, "ERROR", self.DENY)) for i, t in enumerate(agy_port.DENY_CHECK_TOOLS, 1)]

    def test_passes_when_every_wanted_tool_was_denied_by_the_hook(self):
        self.assertEqual(agy_port.DENY_CHECK_TOOLS, ("search_web", "invoke_subagent"))
        self.assertEqual(self.verdict(*self.denied_all()), (True, None))

    def test_the_measured_deny_check_events_pass(self):
        """2026-11-22 の実際のイベントの形で合格する（invoke_subagent はエラーの文が無いので、返事の引用で印を読む）。"""
        a = agy_port.tool_attempts(self.stream(*MEASURED_DENY_CHECK), agy_port.DENY_CHECK_TOOLS)
        got = {t: [(x["state"], x["evidence"], x["subagents"], x["ran"]) for x in v] for t, v in a.items()}
        self.assertEqual(got, {"search_web": [("ERROR", "tool_info", None, False)],
                               "invoke_subagent": [("ERROR", "reply_quote", 0, False)]})
        self.assertEqual(agy_port.deny_verdict(a), (True, None))

    def test_a_subagent_step_needs_the_quote_and_no_subagent(self):
        """下位エージェントの形のステップは、返事に deny の文の引用が無いか、下位エージェントが作られていれば、印にしない。"""
        reply = MEASURED_REPLY.replace(agy_port.SUBAGENT_HOOK_REASON, "unknown error")
        no_quote = MEASURED_DENY_CHECK[:-1] + [{"event": "result", "result": {
            "conversation_id": "c", "status": "SUCCESS", "response": reply}}]
        created = json.loads(json.dumps(MEASURED_DENY_CHECK))
        created[3]["step_update"]["subagent_info"] = {"subagents": [{"conversation_id": "sub"}]}
        for events in (no_quote, created):
            ok, why = self.verdict(*events)
            self.assertFalse(ok)
            self.assertEqual(why, "フックの deny の印が無い: invoke_subagent")

    def test_the_subagent_reason_is_the_one_the_hook_script_returns(self):
        script = (agy_port.ASSETS / "deny_subagent.sh").read_text(encoding="utf-8")
        self.assertIn(f'"reason": "{agy_port.SUBAGENT_HOOK_REASON}"', script)

    def test_a_tool_that_was_not_called_is_not_confirmed(self):
        ok, why = self.verdict(*[s for s in self.denied_all() if s["step_update"]["tool_name"] != "invoke_subagent"])
        self.assertFalse(ok)
        self.assertIn("invoke_subagent", why)

    def test_a_tool_that_ran_or_failed_without_the_hook_fails(self):
        ok, why = self.verdict(*self.denied_all(search_web=step(1, "search_web", "DONE")))
        self.assertFalse(ok)
        self.assertIn("search_web", why)
        ok, why = self.verdict(*self.denied_all(invoke_subagent=step(2, "invoke_subagent", "ERROR", "network unreachable")))
        self.assertFalse(ok)
        self.assertIn("invoke_subagent", why)

    def test_attempts_read_tools_outside_the_denied_list(self):
        self.assertNotIn("generate_image", agy_port.DENIED_TOOLS)
        a = agy_port.tool_attempts(self.stream(step(1, "generate_image", "ERROR", "no image model")), ("generate_image",))
        self.assertEqual(a["generate_image"][0]["state"], "ERROR")
        self.assertFalse(a["generate_image"][0]["hook_denied"])

    def test_prompt_names_the_tools_and_is_not_a_coding_task(self):
        p = agy_port.DENY_CHECK_PROMPT.format(tools=", ".join(agy_port.DENY_CHECK_TOOLS))
        for t in agy_port.DENY_CHECK_TOOLS:
            self.assertIn(t, p)
        self.assertIn("not a coding task", p)
        self.assertIn("quote the error message", p)   # 下位エージェントの印は返事の引用で読む

    def test_prompt_gives_an_argument_example_for_every_default_tool(self):
        """引数の例が無い道具は、モデルが呼ばずに飛ばしうる（呼ばれなかった → 落ち）。文面のひな形そのものに道具ごとの例を書く。"""
        template = agy_port.DENY_CHECK_PROMPT.format(tools="")
        for t in agy_port.DENY_CHECK_TOOLS:
            self.assertIn(f"for {t},", template)


class AuditTests(unittest.TestCase):
    def test_denied_tool_steps_read_state_and_hook_error(self):
        text = "\n".join(json.dumps(e) for e in [
            {"event": "init", "init": {"model": "m", "conversation_id": "c"}},
            step(0, "run_command", "DONE"),
            step(1, "search_web", "ACTIVE"),
            step(1, "search_web", "ERROR", "Tool call denied by pre-tool hook: Web access is not available in this environment."),
            step(2, "invoke_subagent", "ERROR", "tool call denied by pre-tool hook: Subagents are not available in this environment."),
            step(3, "read_url_content", "ERROR", "dial tcp: lookup example.com: no such host"),
            step(4, "webview_browser", "DONE"),
            {"event": "result", "result": {"status": "SUCCESS", "conversation_id": "c"}}])
        steps = agy_port.denied_tool_steps(agy_stream.parse(text)["steps"], agy_port.step_errors(text))
        got = {s["index"]: (s["tool"], s["state"], s["hook_denied"], s["ran"]) for s in steps}
        self.assertEqual(got, {1: ("search_web", "ERROR", True, False),        # フックが止めた：無効にしない
                               2: ("invoke_subagent", "ERROR", True, False),
                               3: ("read_url_content", "ERROR", False, False),  # ほかのエラーでも走っていない
                               4: ("webview_browser", "DONE", False, True)})    # 走った：無効

    def test_the_measured_subagent_error_is_not_counted_as_ran(self):
        """2026-11-22 の実際の invoke_subagent のステップ（ERROR・エラーの文なし）は、走ったと数えない（走りを無効にしない）。
        走りの後の検査は返事を読まないので、hook_denied は False のまま。"""
        text = "\n".join(json.dumps(e) for e in MEASURED_DENY_CHECK)
        steps = agy_port.denied_tool_steps(agy_stream.parse(text)["steps"], agy_port.step_errors(text))
        got = {s["tool"]: (s["state"], s["hook_denied"], s["ran"]) for s in steps}
        self.assertEqual(got, {"search_web": ("ERROR", True, False), "invoke_subagent": ("ERROR", False, False)})

    def test_turn_valid_only_when_no_denied_tool_ran(self):
        ok = {"denied_tools": [{"ran": False}], "hooks_sha256_after": "h"}
        self.assertTrue(agy_port.turn_valid(ok, "h"))
        self.assertFalse(agy_port.turn_valid(dict(ok, denied_tools=[{"ran": False}, {"ran": True}]), "h"))
        self.assertFalse(agy_port.turn_valid(ok, "other"))
        self.assertFalse(agy_port.turn_valid(dict(ok, model_error="報告が無い"), "h"))

    def test_hook_scripts_are_part_of_the_post_run_check(self):
        """root の実装役は chmod 444 を越えて書けるので、hooks.json だけでなくスクリプトの実体も走りの後に照らす。"""
        sbx = {"home": "/root", "uid": 0}
        exp = agy_port.expected_hooks(sbx)
        self.assertEqual(sorted(exp), sorted(["/root/.gemini/config/hooks.json"] +
                                             [f"{agy_port.HOOKS_DIR}/{s}" for s in agy_port.HOOK_SCRIPTS]))
        self.assertEqual(exp["/root/.gemini/config/hooks.json"],
                         hashlib.sha256((agy_port.ASSETS / "hooks.json").read_bytes()).hexdigest())
        fp = agy_port.hooks_fingerprint(exp)
        listing = "".join(f"{d}  {n}\n" for n, d in exp.items())
        self.assertEqual(agy_port.hooks_fingerprint(agy_port.parse_sha256sum(listing, list(exp))), fp)
        script = f"{agy_port.HOOKS_DIR}/deny_web.sh"
        changed = dict(exp, **{script: "0" * 64})
        self.assertNotEqual(agy_port.hooks_fingerprint(changed), fp)          # スクリプトの書き換えで指紋が変わる
        gone = agy_port.parse_sha256sum(listing.replace(f"  {script}\n", "  /elsewhere\n"), list(exp))
        self.assertEqual(gone[script], "missing")
        self.assertNotEqual(agy_port.hooks_fingerprint(gone), fp)             # 消されても変わる
        self.assertFalse(agy_port.turn_valid({"denied_tools": [], "hooks_sha256_after": agy_port.hooks_fingerprint(changed)}, fp))

    def test_timeout_output_keeps_bytes_and_text(self):
        e = subprocess.TimeoutExpired(["agy"], 1, output=b'{"event": "init"}\n', stderr=b"late\n")
        self.assertEqual(agy_port.timeout_output(e), '{"event": "init"}\nlate\n')
        self.assertEqual(agy_port.timeout_output(subprocess.TimeoutExpired(["agy"], 1, output="x\n")), "x\n")
        self.assertEqual(agy_port.timeout_output(subprocess.TimeoutExpired(["agy"], 1)), "")

    def test_timeout_output_from_a_real_timeout(self):
        code = "import time; print('{\"event\": \"init\"}', flush=True); time.sleep(30)"
        with self.assertRaises(subprocess.TimeoutExpired) as cm:
            sandbox.sh3([sys.executable, "-c", code], timeout=3)
        self.assertIn('{"event": "init"}', agy_port.timeout_output(cm.exception))

    def test_timeout_kind_reads_both_the_hard_limit_and_agy_print_timeout(self):
        self.assertEqual(agy_port.timeout_kind(124, {}, ""), "hard")
        self.assertEqual(agy_port.timeout_kind(3, {"finished_reason": "print_timeout"}, ""), "print_timeout")
        self.assertEqual(agy_port.timeout_kind(0, {"finished_reason": "print_timeout"}, ""), "print_timeout")   # 古い版は符号 0
        self.assertEqual(agy_port.timeout_kind(3, {}, "...\n[agy] print timeout after 3580s with turn in progress\n"),
                         "print_timeout")
        self.assertIsNone(agy_port.timeout_kind(3, {"finished_reason": "error"}, 'AGY_ERROR: {"short_error":"x"}\n'))
        self.assertIsNone(agy_port.timeout_kind(0, {"status": "SUCCESS"}, ""))

    def test_print_timeout_from_a_stream(self):
        text = "\n".join(json.dumps(e) for e in (
            {"event": "init", "init": {"model": "gemini-4-hydrogen-medium", "conversation_id": "c1"}},
            step(0, "run_command", "DONE"),
            {"event": "result", "result": {"status": "SUCCESS", "finished_reason": "print_timeout", "conversation_id": "c1"}},
        )) + "\n[agy] print timeout after 3580s with turn in progress; returning partial output\n"
        parsed = agy_stream.parse(text)
        self.assertEqual(agy_port.timeout_kind(3, parsed["outcome"], text), "print_timeout")

    def test_turn_problem_names_why_a_turn_is_not_ok(self):
        ok = {"agent": {"rc": 0, "timeout": None}, "complete": True, "outcome": {"status": "SUCCESS"},
              "denied_tools": [{"tool": "search_web", "ran": False}], "valid": True}
        self.assertIsNone(agy_port.turn_problem(ok))
        self.assertTrue(agy_port.turn_problem(dict(ok, model_error="報告が無い", valid=False)).startswith("model:"))
        self.assertEqual(agy_port.turn_problem(dict(ok, denied_tools=[{"tool": "invoke_subagent", "ran": True}], valid=False)),
                         "invalid: denied tool ran: invoke_subagent")
        self.assertEqual(agy_port.turn_problem(dict(ok, valid=False)), "invalid: hooks changed")
        self.assertEqual(agy_port.turn_problem(dict(ok, agent={"rc": 3, "timeout": "print_timeout"})), "timeout: print_timeout")
        self.assertTrue(agy_port.turn_problem(dict(ok, agent={"rc": 3, "timeout": None},
                                                   outcome={"status": "ERROR"})).startswith("agent:"))
        self.assertTrue(agy_port.turn_problem(dict(ok, complete=False)).startswith("agent:"))

    def test_quota_guard(self):
        guard = agy_port.load_config()["quota_guard"]
        self.assertEqual(guard, {"gemini-weekly": 0.05, "gemini-5h": 0.10}, "枯渇寸前の安全弁だけ（旧 35% の床ではない）")
        ok = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.2}, "gemini-5h": {"remaining_fraction": 0.2}}}
        low = {"buckets": {"gemini-weekly": {"remaining_fraction": 0.04}, "gemini-5h": {"remaining_fraction": 0.9}}}
        self.assertEqual(agy_port.quota_ok(ok, guard), (True, None))
        self.assertFalse(agy_port.quota_ok(low, guard)[0])
        self.assertFalse(agy_port.quota_ok(None, guard)[0])
        self.assertNotIn("quota_floor", agy_port.load_config())


if __name__ == "__main__":
    unittest.main()
