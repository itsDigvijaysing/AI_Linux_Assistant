"""Engine logic without audio or models: tool parsing, wake word, routing flow, gate (fake executor)."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import apps as appdb  # noqa: E402
from ai_linux.engine import brain as brain_mod  # noqa: E402
from ai_linux.engine import tools as T  # noqa: E402
from ai_linux.engine.service import on_ac_power, strip_wake  # noqa: E402
from ai_linux.safety import ActionGate  # noqa: E402
from ai_linux.schema import DEFAULTS  # noqa: E402

APPS = [appdb.App("firefox", "Firefox", ("web", "browser")), appdb.App("org.gnome.Nautilus", "Files", ("folder",)),
        appdb.App("code", "Visual Studio Code", ("editor",)), appdb.App("org.gnome.Ptyxis", "Ptyxis", ("terminal",))]


class FakeExecutor:
    def __init__(self, armed=True):
        self.gate = ActionGate(armed=armed, confirm_globs=["shell.run_command"])
        self.calls = []

    def run_argv(self, argv, *, tool_name, args=None, confirm=None, resource_caps=True, timeout=20):
        d = self.gate.check(tool_name, args)
        if not d.allowed:
            return {"error": "denied", "denied": True}
        self.calls.append(argv)
        return {"returncode": 0, "stdout": "", "stderr": ""}

    def run_shell(self, command, *, tool_name, args=None, confirm=None, timeout=20, resource_caps=True):
        if "rm -rf /" in command:
            return {"error": "refused", "refused": "catastrophic"}
        if confirm is None or not confirm(None):
            return {"error": "denied: action was not confirmed", "denied": True}
        self.calls.append(command)
        return {"returncode": 0, "stdout": "6.12.0\n", "stderr": ""}


def env(armed=True, confirm=None):
    return T.Env(FakeExecutor(armed), confirm=confirm, apps=lambda: APPS)


class ParseTests(unittest.TestCase):
    def test_volume(self):
        e = env()
        self.assertEqual(T._p_volume("set the volume to 30", e), {"percent": 30})
        self.assertEqual(T._p_volume("volume to thirty five", e), {"percent": 35})
        self.assertEqual(T._p_volume("make it louder", e), {"action": "up"})
        self.assertEqual(T._p_volume("turn it down by 20", e), {"action": "down", "step": 20})
        self.assertEqual(T._p_volume("mute", e), {"action": "mute"})
        self.assertEqual(T._p_volume("unmute the sound", e), {"action": "unmute"})
        self.assertIsNone(T._p_volume("volume", e))

    def test_brightness_and_toggles(self):
        e = env()
        self.assertEqual(T._p_level("brightness to max"), {"percent": 100})
        self.assertEqual(T._p_level("dim the screen"), {"action": "down"})
        self.assertEqual(T._p_toggle("turn night light off", e), {"on": False})
        self.assertEqual(T._p_dnd("silence my notifications", e), {"on": True})
        self.assertEqual(T._p_dnd("show notifications again", e), {"on": False})
        self.assertIsNone(T._p_toggle("night light", e))

    def test_open_search_media_settings(self):
        e = env()
        self.assertEqual(T._p_open("open firefox", e), {"target": "firefox"})
        self.assertEqual(T._p_open("please launch the files app", e), {"target": "org.gnome.Nautilus"})
        self.assertEqual(T._p_open("open github.com", e), {"target": "github.com"})
        self.assertIsNone(T._p_open("open zorkmid", e))
        self.assertEqual(T._p_search("search youtube for lofi beats", e), {"query": "lofi beats", "site": "youtube"})
        self.assertEqual(T._p_search("look up pasta recipes on wikipedia", e),
                         {"query": "pasta recipes", "site": "wikipedia"})
        self.assertEqual(T._p_media("skip this song", e), {"action": "next"})
        self.assertEqual(T._p_settings("open wi-fi settings", e), {"panel": "wifi"})
        self.assertEqual(T._p_files("show my downloads", e), {"folder": "downloads"})
        self.assertEqual(T._p_clipboard("copy hello world to the clipboard", e), {"action": "copy", "text": "hello world"})
        self.assertEqual(T._p_voice("use a female voice", e), {"voice": "supertonic:F1"})
        self.assertEqual(T._p_status("how much ram am I using", e), {"aspect": "memory"})

    def test_number_words(self):
        self.assertEqual(T.number("set it to seventy"), 70)
        self.assertEqual(T.number("a hundred percent"), 100)
        self.assertIsNone(T.number("louder please"))

    def test_menu_has_none_and_unique_names(self):
        m = T.menu()
        self.assertIn(T.NONE, m)
        self.assertEqual(len(m), len(T.TOOLS) + 1)
        for t in T.TOOLS:
            self.assertEqual(t.schema()["function"]["name"], t.name)


class RunTests(unittest.TestCase):
    def test_typed_tools_build_argv(self):
        e = env()
        self.assertTrue(T.run_volume({"percent": 40}, e).ok)
        self.assertEqual(e.executor.calls[-1], ["wpctl", "set-volume", T.SINK, "40%"])
        r = T.run_open({"target": "firefox"}, e)
        self.assertEqual(r.say, "Opening Firefox.")
        self.assertEqual(e.executor.calls[-1], ["setsid", "-f", "gtk-launch", "firefox"])
        self.assertFalse(T.run_open({"target": "/no/such/path"}, e).ok)

    def test_disarmed_gate_denies(self):
        e = env(armed=False)
        r = T.run_volume({"action": "up"}, e)
        self.assertFalse(r.ok)
        self.assertEqual(e.executor.calls, [])

    def test_shell_needs_confirmation_and_denylist(self):
        self.assertFalse(T.run_command({"command": "uname -r"}, env(confirm=lambda r: False)).ok)
        r = T.run_command({"command": "uname -r"}, env(confirm=lambda r: True))
        self.assertTrue(r.ok and r.summarise and "6.12.0" in r.output)
        self.assertIn("won't do that", T.run_command({"command": "rm -rf /"}, env(confirm=lambda r: True)).say)


class WakeAndPowerTests(unittest.TestCase):
    def test_strip_wake(self):
        self.assertEqual(strip_wake("Computer, open firefox.", "computer"), "open firefox.")
        self.assertEqual(strip_wake("hey computer", "computer"), "")
        self.assertIsNone(strip_wake("we bought a new computer case yesterday at the shop", "computer"))
        self.assertIsNone(strip_wake("open firefox", "computer"))
        self.assertEqual(strip_wake("hey jarvis what time is it", "hey jarvis"), "what time is it")

    def test_ac_power(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(on_ac_power(root))  # no battery: a desktop
            for name, kind, online in (("BAT1", "Battery", None), ("ACAD", "Mains", "0")):
                (root / name).mkdir()
                (root / name / "type").write_text(kind)
                if online is not None:
                    (root / name / "online").write_text(online)
            self.assertFalse(on_ac_power(root))
            (root / "ACAD" / "online").write_text("1")
            self.assertTrue(on_ac_power(root))


class BrainTests(unittest.TestCase):
    def make(self, probs, armed=True, **settings):
        values = {**DEFAULTS, "ollama_url": "http://x", **settings}
        said, cues = [], []
        e = env(armed)
        e.cue = cues.append
        b = brain_mod.Brain(values.get, e, speak=said.append)

        class FakeJiwo:
            max_options, device = 255, "GPU"

            def ask(self, text, q, options):
                return {k: probs.get(k, 0.0) for k in options}
        b._small, b._small_key = FakeJiwo(), (values["small_model"], values["small_device"])
        return b, said, cues

    def test_confident_tool_runs_without_big_model(self):
        b, said, cues = self.make({"set_volume": 0.97, "none": 0.03})
        with mock.patch.object(brain_mod.llm, "chat", side_effect=AssertionError("big model called")):
            turn = b.handle("volume to 25", "text", threading.Event())
        self.assertEqual(turn["route"], "small")
        self.assertEqual(turn["tools"][0]["args"], {"percent": 25})
        self.assertEqual((said, cues, turn["reply"]), ([], ["ok"], "Volume 25 percent."))  # a chime, not words

    def test_speech_feedback_speaks_results(self):
        b, said, cues = self.make({"set_volume": 0.97, "none": 0.03}, action_feedback="speech")
        b.handle("volume to 25", "text", threading.Event())
        self.assertEqual((said, cues), (["Volume 25 percent."], []))

    def test_answers_are_spoken(self):
        b, said, cues = self.make({"get_time": 0.97})
        b.handle("what time is it", "text", threading.Event())
        self.assertEqual(len(said), 1)
        self.assertEqual(cues, [])

    def test_unsure_goes_to_big_model(self):
        b, said, cues = self.make({"set_volume": 0.5, "none": 0.5})
        chunks = [("text", "It's sunny "), ("text", "today. Enjoy it!"), ("done", {})]
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter(chunks)):
            turn = b.handle("what's it like outside", "text", threading.Event())
        self.assertEqual(turn["route"], "big")
        self.assertEqual(said, ["It's sunny today.", "Enjoy it!"])
        self.assertEqual(cues, ["big"])

    def test_big_only_cues_when_asleep(self):
        b, said, cues = self.make({}, small_model="none")
        for loaded, want in ((True, []), (False, ["big"])):
            cues.clear()
            with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.9, "set_volume": 0.1}), \
                    mock.patch.object(brain_mod.llm, "loaded", return_value=loaded), \
                    mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Hi."), ("done", {})])):
                b.handle("hello there", "text", threading.Event())
            self.assertEqual(cues, want)

    def test_user_details_reach_the_big_model(self):
        b, said, cues = self.make({"none": 0.99}, user_name="Dee", about_me="I live in Pune.")
        seen = []

        def chat(base, model, msgs, **kw):
            seen.append(msgs[0]["content"])
            return iter([("text", "Hi Dee."), ("done", {})])
        with mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            b.handle("who am I", "text", threading.Event())
        self.assertIn("Their name is Dee. I live in Pune.", seen[0])
        self.assertIn("You are Phoenix", seen[0])
        b2, *_ = self.make({})
        self.assertEqual(b2.about_user(), "")

    def test_llm_options(self):
        b, *_ = self.make({}, context_length=4096)
        self.assertEqual(b.llm_options(temperature=0), {"num_ctx": 4096, "temperature": 0})
        b, *_ = self.make({}, big_device="cpu")
        self.assertEqual(b.llm_options(), {"num_ctx": 8192, "num_gpu": 0})

    def test_choice_filled_by_jiwo(self):
        b, said, cues = self.make({"open_settings": 0.95, "sound": 0.9, "none": 0.0})
        with mock.patch.object(T, "_p_settings", return_value=None), \
                mock.patch.object(brain_mod.llm, "chat", side_effect=AssertionError("big model called")):
            args, who = b.fill(b.tool("open_settings"), "the page where my speakers are", threading.Event())
        self.assertEqual((args, who), ({"panel": "sound"}, "jiwo-0.8b"))

    def test_exact_level_goes_to_big_model(self):
        self.assertEqual(T._p_level("put the screen at half brightness"), {"percent": 50})
        b, *_ = self.make({"exact": 0.95, "up": 0.03})
        call = [("tools", [{"name": "set_screen_brightness", "arguments": {"percent": 50}}]), ("done", {})]
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter(call)):
            args, who = b.fill(b.tool("set_screen_brightness"), "a medium level please", threading.Event())
        self.assertEqual((args, who), ({"percent": 50}, "qwen3.5:4b"))

    def test_disarmed_reports_denied(self):
        b, said, cues = self.make({"set_volume": 0.97}, armed=False)
        turn = b.handle("volume to 25", "text", threading.Event())
        self.assertEqual(turn["tools"][0]["verdict"], "denied")
        self.assertIn("turned off", said[0])
        self.assertEqual(cues, ["fail"])

    def test_yes_no(self):
        b, *_ = self.make({"yes": 0.9, "no": 0.05, "unclear": 0.05})
        self.assertTrue(b.yes_no("Should I?", "Yes."))
        self.assertTrue(b.yes_no("Should I?", "go ahead please"))
        self.assertFalse(b.yes_no("Should I?", "No, cancel that"))
        self.assertIsNone(b.yes_no("Should I?", ""))
        self.assertTrue(b.yes_no("Should I?", "sounds good to me"))  # the small model decides
        b, *_ = self.make({"yes": 0.3, "no": 0.2, "unclear": 0.5})
        self.assertIsNone(b.yes_no("Should I?", "what will that do"))

    def test_addressed(self):
        b, *_ = self.make({"assistant": 0.2, "other": 0.8})
        self.assertFalse(b.addressed("mum, dinner is ready"))
        b, *_ = self.make({"assistant": 0.6, "other": 0.4})
        self.assertTrue(b.addressed("open the browser"))
        with mock.patch.object(b, "decide", side_effect=RuntimeError):
            self.assertTrue(b.addressed("anything"))  # errs towards answering


class PasteTests(unittest.TestCase):
    def test_write_text_pastes_and_restores(self):
        e = env()
        e.compose = lambda request: "Thanks, see you at 5."
        e.executor.run_argv = mock.Mock(side_effect=lambda argv, **kw: {
            "returncode": 0, "stdout": "old clip" if argv[0] == "wl-paste" else "", "stderr": ""})
        with mock.patch.object(T.threading, "Timer") as timer, mock.patch.object(T.time, "sleep"):
            res = T.run_write({"request": "reply that I'll be there"}, e)
        argvs = [c.args[0] for c in e.executor.run_argv.call_args_list]
        self.assertEqual(argvs, [["wl-paste", "--no-newline", "--type", "text/plain"],
                                 ["wl-copy", "--", "Thanks, see you at 5."], T.CTRL_V])
        self.assertTrue(res.ok)
        self.assertEqual(timer.call_args.args[0], 1.5)  # the old clipboard comes back

    def test_without_ydotool_it_stays_on_the_clipboard(self):
        e = env()
        e.executor.run_argv = mock.Mock(side_effect=lambda argv, **kw: {"error": "not found"}
                                        if argv[0] == "ydotool" else {"returncode": 0, "stdout": "", "stderr": ""})
        with mock.patch.object(T.time, "sleep"):
            res = T.paste_text(e, "hello", {})
        self.assertTrue(res.ok and res.speak)
        self.assertIn("Control V", res.say)


class McpTests(unittest.TestCase):
    SERVER = Path(__file__).with_name("fake_mcp_server.py")

    def spec(self, **kw):
        return {"name": "demo", "command": sys.executable, "args": [str(self.SERVER)], **kw}

    def test_lists_and_calls_tools(self):
        from ai_linux.engine import mcp_client
        tools = mcp_client.McpTools([self.spec(confirm=False)])
        try:
            names = [t.name for t in tools.tools()]
            self.assertEqual(names, ["demo_echo", "demo_fail"])
            echo = tools.tools()[0]
            self.assertEqual((echo.gate_name, echo.required), ("mcp.demo.echo", ("text",)))
            res = echo.run({"text": "hi"}, env())
            self.assertEqual((res.ok, res.output, res.summarise), (True, "echo: hi", True))
            res = tools.tools()[1].run({}, env())
            self.assertFalse(res.ok)
        finally:
            tools.close()
        self.assertIsNotNone(tools.servers[0].proc.poll())  # stopped

    def test_confirms_and_gates(self):
        from ai_linux.engine import mcp_client
        tools = mcp_client.McpTools([self.spec()])
        try:
            echo = tools.tools()[0]
            asked = []
            res = echo.run({"text": "hi"}, env(confirm=lambda req: asked.append(req) or False))
            self.assertFalse(res.ok)
            self.assertEqual(asked[0].tool_name, "mcp.demo.echo")
            self.assertFalse(ActionGate(armed=False).check(echo.gate_name).allowed)
        finally:
            tools.close()

    def test_broken_server_is_reported(self):
        from ai_linux.engine import mcp_client
        notes = []
        tools = mcp_client.McpTools([{"name": "nope", "command": "/nonexistent/server"}],
                                    notice=lambda level, text: notes.append(text))
        self.assertEqual(tools.tools(), [])
        self.assertIn("nope", notes[0])


class EngineSettingsTests(unittest.TestCase):
    def test_missing_settings_use_defaults(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {"voice": "supertonic:F1"})
        self.assertEqual(e.s["escalate_threshold"], DEFAULTS["escalate_threshold"])
        self.assertEqual((e.s["voice"], e.s["wake_word"]), ("supertonic:F1", "phoenix"))


if __name__ == "__main__":
    unittest.main()
