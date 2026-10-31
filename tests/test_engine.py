"""Engine logic without audio or models: tool parsing, wake word, routing flow, gate (fake executor)."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import numpy as np
import importlib.util  # noqa: E402
if importlib.util.find_spec("numpy") is None:
    raise unittest.SkipTest("needs numpy (the engine's requirements)")
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

    def test_model_level_arguments_in_any_shape(self):
        e = env()  # the big model fills these; the schema asks for an integer but nothing enforces it
        for value, want in (("40%", "40%"), (" 40 % ", "40%"), ("fifty", "50%"), (55.6, "55%"), ("100", "100%")):
            self.assertTrue(T.run_volume({"percent": value}, e).ok, value)
            self.assertEqual(e.executor.calls[-1][-1], want)
        self.assertTrue(T.run_volume({"action": "up", "step": "5%"}, e).ok)
        self.assertEqual(e.executor.calls[-1][-1], "5%+")
        self.assertTrue(T.run_brightness({"percent": "30%"}, e).ok)
        self.assertEqual(e.executor.calls[-1][-1], "30%")
        before = len(e.executor.calls)
        for junk in ("", "loud", [], {}, None, True):
            self.assertFalse(T.run_volume({"percent": junk}, e).ok, junk)  # asks instead of failing the turn
            self.assertFalse(T.run_brightness({"percent": junk}, e).ok, junk)
        self.assertEqual(len(e.executor.calls), before)

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
        self.assertEqual(strip_wake("Stop Phoenix.", "phoenix"), "Stop")  # a request said before the name
        self.assertEqual(strip_wake("Good night, Phoenix", "phoenix"), "Good night")
        self.assertEqual(strip_wake("Hey Phoenix", "phoenix"), "")  # only calling it
        self.assertEqual(strip_wake("Wake up Phoenix!", "phoenix"), "")
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

    def test_missing_big_model_says_so(self):
        b, said, _ = self.make({"none": 0.99})
        with mock.patch.object(b, "_handle", side_effect=brain_mod.llm.LLMError("model 'qwen3.5:4b' not found")):
            turn = b.handle("tell me a story", "text", threading.Event())
        self.assertEqual(turn["status"], "failed")
        self.assertIn("isn't downloaded", said[0])
        with mock.patch.object(b, "_handle", side_effect=brain_mod.llm.LLMError("connection refused")):
            b.handle("tell me a story", "text", threading.Event())
        self.assertIn("can't reach", said[1])

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

    def test_remember_needs_no_model(self):
        b, said, cues = self.make({})
        saved = []
        b.env.knowledge = mock.Mock(add=lambda text, kept, source: (saved.append((text, kept)) or mock.Mock(kept=kept), ""))
        with mock.patch.object(b, "route", side_effect=AssertionError("routed")):
            turn = b.handle("Remember that my sister is Priya.", "text", threading.Event())
        self.assertEqual(saved, [("Their sister is Priya", False)])
        self.assertEqual((turn["route"], turn["tools"][0]["name"]), ("rule", "remember"))

    def test_sleep_words_need_no_model(self):
        b, said, cues = self.make({})
        slept = []
        b.env.sleep = lambda: slept.append(True)
        with mock.patch.object(b, "route", side_effect=AssertionError("routed")):
            for text in ("Stop", "that's all, thanks", "you can go to sleep now", "never mind"):
                turn = b.handle(text, "text", threading.Event())
                self.assertEqual((turn["route"], turn["tools"][0]["name"]), ("rule", "go_to_sleep"), text)
        self.assertEqual(len(slept), 4)
        self.assertEqual(set(cues), {"sleep"})  # its own sound, not the "done" chime

    def test_greetings_and_help_answer_without_a_model(self):
        b, said, cues = self.make({})
        with mock.patch.object(b, "route", side_effect=AssertionError("routed")):
            for text in ("hi", "Good morning!", "thank you", "what can you do?"):
                turn = b.handle(text, "text", threading.Event())
                self.assertEqual((turn["route"], turn["tools"]), ("rule", []), text)
        self.assertTrue(said[1].startswith("Good morning"))
        self.assertIn("open apps", said[3])

    def test_small_talk_answers_without_a_model(self):
        b, said, cues = self.make({}, wake_word="nova")
        talk = ("what are you doing?", "are you there?", "how are you feeling?", "hi, how are you", "who are you",
                "I'm fine", "good job", "what are your thoughts")
        with mock.patch.object(b, "route", side_effect=AssertionError("routed")):
            for text in talk:
                self.assertEqual(b.handle(text, "text", threading.Event())["route"], "rule", text)
        self.assertEqual(len(said), len(talk))
        self.assertIn("I'm Nova", said[4])

    def test_questions_with_content_still_reach_the_models(self):
        from ai_linux.engine.quick import quick
        for text in ("what are your thoughts on electric cars", "what are you doing tomorrow at 5",
                     "how are you able to open apps", "what is up with my wifi"):
            self.assertIsNone(quick(text), text)

    def test_youtube_play_searches_youtube(self):
        b, said, cues = self.make({})
        with mock.patch.object(b, "route", side_effect=AssertionError("routed")):
            turn = b.handle("open YouTube and play Believer", "text", threading.Event())
        self.assertEqual(turn["tools"][0]["args"], {"query": "Believer", "site": "youtube"})
        self.assertIn("youtube.com/results?search_query=Believer", b.env.executor.calls[0][-1])

    def test_cold_big_model_says_it_is_waking(self):
        b, said, cues = self.make({"none": 0.99})
        with mock.patch.object(brain_mod.llm, "loaded", return_value=False), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Sure."), ("done", {})])):
            b.handle("tell me a joke", "text", threading.Event())
        self.assertEqual(said, ["Waking up the big model.", "Sure."])
        said.clear()
        with mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Sure."), ("done", {})])):
            b.handle("another", "text", threading.Event())
        self.assertEqual(said, ["Sure."])

    def test_empty_big_answer_plays_unclear(self):
        b, said, cues = self.make({"none": 0.99})
        with mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("done", {})])):
            turn = b.handle("blorp", "text", threading.Event())
        self.assertIn("unclear", cues)
        self.assertEqual(turn["reply"], "Sorry, I didn't get that.")

    def test_memory_rules(self):
        self.assertEqual(T.parse_memory("Always remember I'm vegetarian"),
                         ("remember", {"fact": "They're vegetarian", "always": True}))
        self.assertEqual(T.parse_memory("Forget that I live in Pune."), ("forget", {"fact": "They live in Pune"}))
        for vague in ("remember to buy milk", "do you remember my name", "remember this", "forget it",
                      "forget about the timer"):
            self.assertIsNone(T.parse_memory(vague), vague)

    def test_unsure_goes_to_big_model(self):
        b, said, cues = self.make({"set_volume": 0.5, "none": 0.5})
        chunks = [("text", "It's sunny "), ("text", "today. Enjoy it!"), ("done", {})]
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter(chunks)):
            turn = b.handle("what's it like outside", "text", threading.Event())
        self.assertEqual(turn["route"], "big")
        self.assertEqual(said, ["It's sunny today.", "Enjoy it!"])
        self.assertEqual(cues, ["big"])

    def test_unsure_action_asks_first(self):
        for answer, ran in ((True, True), (False, False)):
            b, said, cues = self.make({"set_volume": 0.6, "none": 0.4})
            asked = []
            b.env.confirm = lambda req: asked.append(req) or answer
            with mock.patch.object(brain_mod.llm, "chat", side_effect=AssertionError("guessed with the big model")):
                turn = b.handle("volume to 30", "text", threading.Event())
            self.assertEqual(asked[0].detail, "Did you mean volume, 30?")
            self.assertEqual(bool(turn["tools"]), ran)
            self.assertEqual(turn["alts"], {"set_volume": 0.6, "none": 0.4})
            if not ran:
                self.assertEqual((turn["status"], said), ("declined", ["Okay, I won't."]))

    def test_unclear_request_gets_no_action_tools(self):
        b, said, cues = self.make({"set_volume": 0.4, "go_to_sleep": 0.35, "none": 0.25})
        b.env.confirm = mock.Mock(return_value=True)
        seen = []

        def chat(base, model, msgs, tools=None, **kw):
            seen.append(tools)
            return iter([("text", "Sure, what would you like?"), ("done", {})])
        with mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            turn = b.handle("please don't sleep on it", "text", threading.Event())
        self.assertEqual((turn["tools"], seen), ([], [None]))  # no action tools offered
        b.env.confirm.assert_not_called()

    def test_thats_wrong_marks_the_previous_request(self):
        b, said, cues = self.make({"set_volume": 0.97, "none": 0.03})
        first = b.handle("volume to 25", "text", threading.Event())
        turn = b.handle("that's wrong", "text", threading.Event())
        self.assertIs(turn["marks"], first)
        self.assertEqual((first["status"], turn["route"]), ("wrong", "rule"))
        self.assertIn("noted", said[-1])

    def test_dont_sleep_and_hello_question_are_rules(self):
        from ai_linux.engine.quick import quick
        self.assertEqual(quick("please don't sleep").say, "Okay, I'm still here.")
        self.assertEqual(quick("please don't sleep").tool, "")  # never goes to sleep
        self.assertTrue(quick("Hello?").say.startswith(("Hi", "Good")))

    def test_big_only_cues_when_asleep(self):
        b, said, cues = self.make({}, small_model="none")
        for loaded, want in ((True, []), (False, ["big"])):
            cues.clear()
            with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.9, "set_volume": 0.1}), \
                    mock.patch.object(brain_mod.llm, "loaded", return_value=loaded), \
                    mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Hi."), ("done", {})])):
                b.handle("tell me a joke", "text", threading.Event())
            self.assertEqual(cues, want)

    def test_plain_chat_gets_no_action_tools(self):
        b, said, cues = self.make({}, small_model="none")
        seen = []

        def chat(base, model, msgs, tools=None, **kw):
            seen.append({t["function"]["name"] for t in tools or []})
            return iter([("text", "Hello!"), ("done", {})])
        with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.9, "get_time": 0.1}), \
                mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            turn = b.handle("tell me a fun fact", "text", threading.Event())
            b.handle("my sister lives in Pune", "text", threading.Event())
        self.assertEqual(seen, [set(), {"remember", "forget"}])  # memory only when they say something about themselves
        self.assertEqual(turn["tools"], [])
        seen.clear()
        with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.3, "get_time": 0.6,
                                                                         "set_volume": 0.1}), \
                mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            b.handle("is it late", "text", threading.Event())
        self.assertEqual(seen, [set()])  # unsure and nobody to ask: answer, never guess an action

    def test_saving_a_fact_never_replaces_the_answer(self):
        b, said, cues = self.make({}, small_model="none")
        b.env.knowledge = mock.Mock(add=mock.Mock(return_value=(mock.Mock(kept=False), "")),
                                    prompt=mock.Mock(return_value=""))
        replies = iter([iter([("tools", [{"name": "remember", "arguments": {"fact": "Their name is Dee",
                                                                             "always": True}}]), ("done", {})]),
                        iter([("text", "Nice to meet you, Dee."), ("done", {})])])
        with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.9}), \
                mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", side_effect=lambda *a, **k: next(replies)):
            turn = b.handle("my name is Dee", "text", threading.Event())
        self.assertEqual(turn["reply"], "Nice to meet you, Dee.")
        b.env.knowledge.add.assert_called_once_with("Their name is Dee", kept=False, source="learned")

    def test_new_session_drops_the_turn_still_running(self):
        b, said, cues = self.make({}, small_model="none")

        def chat(base, model, msgs, **kw):
            b.new_session()  # Home's New Session while the model answers
            return iter([("text", "Hello!"), ("done", {})])
        with mock.patch.object(brain_mod.llm, "letters", return_value={"none": 0.9}), \
                mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            b.handle("hi there friend", "text", threading.Event())
        self.assertEqual(b.history, [])

    def test_user_details_reach_the_big_model(self):
        import tempfile
        from ai_linux.knowledge import Knowledge
        b, said, cues = self.make({"none": 0.99}, user_name="Dee")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        b.env.knowledge = Knowledge(Path(tmp.name) / "k.json")
        b.env.knowledge.add("I live in Pune", kept=True)
        seen = []

        def chat(base, model, msgs, **kw):
            seen.append(msgs[0]["content"])
            return iter([("text", "Hi Dee."), ("done", {})])
        with mock.patch.object(brain_mod.llm, "chat", side_effect=chat):
            b.handle("who am I", "text", threading.Event())
        self.assertIn("Their name is Dee. Facts they keep: I live in Pune.", seen[0])
        self.assertIn("You are Phoenix", seen[0])
        b2, *_ = self.make({})
        self.assertEqual(b2.about_user(), "")

    def test_big_model_status_waking_then_working(self):
        b, said, cues = self.make({"none": 0.99})
        status = []
        b._status = status.append
        with mock.patch.object(brain_mod.llm, "loaded", return_value=False), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Hi."), ("done", {})])):
            b.handle("tell me a joke", "text", threading.Event())
        self.assertEqual(status, ["Waking up the big model", "The big model is answering"])
        status.clear()
        with mock.patch.object(brain_mod.llm, "loaded", return_value=True), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Hi."), ("done", {})])):
            b.handle("tell me another", "text", threading.Event())
        self.assertEqual(status, ["The big model is answering"])

    def test_web_tools_only_with_web_access(self):
        b, *_ = self.make({})
        self.assertNotIn("web_lookup", [t.name for t in b.tools()])
        b2, *_ = self.make({}, web_access=True)
        self.assertIn("web_lookup", [t.name for t in b2.tools()])

    def test_memory_tools_only_for_the_big_model(self):
        b, *_ = self.make({})
        names = {t.name for t in b.chat_tools()}
        self.assertTrue({"remember", "forget"} <= names)
        self.assertFalse({"remember", "forget"} & {t.name for t in b.tools()})

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

    def test_polish_asks_the_big_model_once_without_tools(self):
        b = brain_mod.Brain({"ollama_url": "http://x", "big_model": "qwen3.5:4b", "context_length": 8192,
                             "big_model_policy": "auto", "ollama_keep_alive": "30m"}.get, env(), speak=lambda s: None)
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "I went "), ("text", "home.")])) as chat:
            self.assertEqual(b.polish("i has went home"), "I went home.")
        msgs = chat.call_args.args[2]
        self.assertIn("i has went home", msgs[-1]["content"])
        self.assertEqual(chat.call_args.kwargs.get("tools"), None)
        self.assertEqual(chat.call_args.kwargs["options"]["temperature"], 0)

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
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp)
        self.token = tmp / "portal-paste-token"
        for patcher in (mock.patch.object(T, "PORTAL_TOKEN", self.token), mock.patch.object(T.time, "sleep"),
                        mock.patch.dict(T._portal, {"broken": False, "denied": False})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def paste(self, ydotoold=True, detect="app", portal_rc=0, ydotool_rc=0):
        e = env()
        def run(argv, **kw):
            if argv[0] == "wl-paste":
                return {"returncode": 0, "stdout": "old clip", "stderr": ""}
            if argv[-1] == "detect":
                return {"returncode": 0, "stdout": detect + "\n", "stderr": ""}
            if "paste" in argv[1:3]:
                return {"returncode": portal_rc, "stdout": "", "stderr": ""}
            if argv[0] == "ydotool":
                return {"returncode": ydotool_rc, "stdout": "", "stderr": ""}
            return {"returncode": 0, "stdout": "", "stderr": ""}
        e.executor.run_argv = mock.Mock(side_effect=run)
        with mock.patch.object(T, "_ydotoold_running", return_value=ydotoold), \
                mock.patch.object(T.threading, "Timer") as timer:
            res = T.paste_text(e, "Thanks, see you at 5.", {})
        steps = [c.args[0] for c in e.executor.run_argv.call_args_list][2:]  # after wl-paste and wl-copy
        return res, steps, timer

    def test_ydotool_pastes_and_the_old_clipboard_comes_back(self):
        res, steps, timer = self.paste()
        self.assertEqual(steps[-1], ["ydotool", "key", *T.YDOTOOL_KEYS["ctrl-v"]])
        self.assertTrue(res.ok and not res.speak)
        self.assertEqual(timer.call_args.args[0], 1.5)

    def test_terminals_get_ctrl_shift_v(self):
        _res, steps, _t = self.paste(detect="terminal")
        self.assertEqual(steps[-1], ["ydotool", "key", *T.YDOTOOL_KEYS["ctrl-shift-v"]])

    def test_saved_portal_token_is_used_first(self):
        self.token.write_text("tok")
        _res, steps, _t = self.paste()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0][2:], ["paste", "--token-file", str(self.token)])

    def test_broken_portal_falls_back_to_ydotool(self):
        self.token.write_text("tok")
        res, steps, _t = self.paste(portal_rc=1)
        self.assertEqual(steps[-1][0], "ydotool")
        self.assertTrue(res.ok and T._portal["broken"])

    def test_without_ydotool_the_portal_asks_once(self):
        res, steps, _t = self.paste(ydotoold=False)
        self.assertEqual([s[2] for s in steps], ["paste"])
        self.assertTrue(res.ok and not res.speak)

    def test_refused_portal_leaves_it_on_the_clipboard(self):
        res, _steps, _t = self.paste(ydotoold=False, portal_rc=3)
        self.assertTrue(res.ok and res.speak)
        self.assertIn("Control V", res.say)
        _res, steps, _t = self.paste(ydotoold=False)
        self.assertEqual(steps, [])  # not asked again this run


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


class McpToolChoiceTests(unittest.TestCase):
    """Per-tool on/off: a big server must not flood the routing menu (fake MCP server, no network)."""
    SERVER = Path(__file__).with_name("fake_mcp_server.py")

    def spec(self, **kw):
        return {"name": "demo", "command": sys.executable, "args": [str(self.SERVER)], "confirm": False, **kw}

    def test_catalog_lists_every_tool_and_tools_skips_the_disabled(self):
        from ai_linux.engine import mcp_client
        tools = mcp_client.McpTools([self.spec()])
        self.addCleanup(tools.close)
        self.assertEqual([t["name"] for t in tools.catalog["demo"]], ["echo", "fail"])
        self.assertEqual(tools.catalog["demo"][0]["description"], "Repeat the text.")
        self.assertEqual([t.name for t in tools.tools({"demo": {"fail"}})], ["demo_echo"])
        self.assertEqual([t.name for t in tools.tools()], ["demo_echo", "demo_fail"])

    def test_long_server_descriptions_stay_short_on_the_menu(self):
        from ai_linux.engine import mcp_client
        long = ("Read the complete contents of a file from the file system as text. Handles various text "
                "encodings and provides detailed error messages if the file cannot be read. Use this tool when "
                "you need to examine the contents of a single file. Only works within allowed directories.")
        server = mock.Mock()
        server.name = "files"
        tool = mcp_client.make_tool(server, {}, {"name": "read_text_file", "description": long})
        self.assertEqual(tool.description, "Read the complete contents of a file from the file system as text.")
        no_stop = mcp_client.make_tool(server, {}, {"name": "x", "description": "word " * 60})
        self.assertLessEqual(len(no_stop.description), 120)  # every option is read on every request
        self.assertEqual(mcp_client.make_tool(server, {}, {"name": "bare"}).description, "bare")

    def test_engine_menu_follows_disabled_tools_and_reports_the_catalog(self):
        from ai_linux.engine.service import Engine
        events = []
        e = Engine(events.append, {"mcp_servers": [self.spec(disabled_tools=["fail"])]})
        e.brain = mock.Mock(extra_tools=[])
        e._load_mcp()
        self.addCleanup(e.mcp.close)
        self.assertEqual([t.name for t in e.brain.extra_tools], ["demo_echo"])
        sent = [ev for ev in events if ev.get("ev") == "mcp_tools"]
        self.assertEqual([t["name"] for t in sent[-1]["servers"]["demo"]], ["echo", "fail"])

    def test_turning_a_tool_off_does_not_restart_the_servers(self):
        from ai_linux.engine import mcp_client
        from ai_linux.engine.service import Engine
        made = []
        real = mcp_client.McpTools

        def counting(*a, **k):
            made.append(1)
            return real(*a, **k)

        e = Engine(lambda ev: None, {"mcp_servers": [self.spec()]})
        e.brain = mock.Mock(extra_tools=[])
        with mock.patch.object(mcp_client, "McpTools", counting):
            e._load_mcp()
            self.addCleanup(e.mcp.close)
            e.update_settings({"mcp_servers": [self.spec(disabled_tools=["echo"])]})
            deadline = time.monotonic() + 2
            while [t.name for t in e.brain.extra_tools] != ["demo_fail"] and time.monotonic() < deadline:
                time.sleep(0.02)
        self.assertEqual([t.name for t in e.brain.extra_tools], ["demo_fail"])
        self.assertEqual(len(made), 1)  # the same running servers

class QuitAndMicTests(unittest.TestCase):
    def test_quit_frees_the_graphics_card(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.brain = mock.Mock(base="http://127.0.0.1:11434", big="qwen3.5:4b")
        with mock.patch("ai_linux.engine.llm.unload") as unload:
            e.stop()
        unload.assert_called_once_with("http://127.0.0.1:11434", "qwen3.5:4b", timeout=2)

    def test_dead_mic_is_reported_with_the_input_name(self):
        from ai_linux.engine.mic_watch import SilenceWatch
        from ai_linux.engine.service import Engine
        events = []
        e = Engine(events.append, {})
        e._mic = SilenceWatch(dead_s=0)
        e._capture = mock.Mock()
        with mock.patch("ai_linux.engine.mic_watch.input_name", return_value="Digital Stereo (IEC958)"):
            e._watch_mic(0.0)  # first: opened again quietly
            self.assertEqual([c.args for c in e._capture.call_args_list], [(False,), (True,)])
            e._mic.restart(0.0)
            e._watch_mic(0.0)  # still silent: now the warning
            e._watch_mic(0.01)
        notes = [ev for ev in events if ev.get("ev") == "notice"]
        self.assertEqual([n["level"] for n in notes], ["error", "info"])
        self.assertIn("Digital Stereo (IEC958)", notes[0]["text"])

    def test_mic_that_recovers_after_reopening_stays_quiet(self):
        from ai_linux.engine.mic_watch import SilenceWatch
        from ai_linux.engine.service import Engine
        events = []
        e = Engine(events.append, {})
        e._mic = SilenceWatch(dead_s=0)
        e._capture = mock.Mock()
        e._watch_mic(0.0)
        e._mic.restart(0.0)
        e._watch_mic(0.01)
        self.assertFalse(any(ev.get("ev") == "notice" for ev in events))
        self.assertFalse(e._mic_reopened)  # a later failure gets its own quiet retry

    def test_quit_before_the_models_load_sends_nothing(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        with mock.patch("ai_linux.engine.llm.unload") as unload:
            e.stop()
        unload.assert_not_called()


class EngineSettingsTests(unittest.TestCase):
    def test_missing_settings_use_defaults(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {"voice": "supertonic:F1"})
        self.assertEqual(e.s["escalate_threshold"], DEFAULTS["escalate_threshold"])
        self.assertEqual((e.s["voice"], e.s["wake_word"]), ("supertonic:F1", "phoenix"))

    def test_secrets_are_kept_in_memory_and_can_be_removed(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.set_secrets({"todoist": "dummy-token"})
        self.assertEqual(e._secrets.get("todoist"), "dummy-token")
        self.assertNotIn("dummy-token", repr(e.s))  # not a setting, so never sent back or saved
        e.set_secrets({"todoist": ""})
        self.assertIsNone(e._secrets.get("todoist"))

class TimerEventTests(unittest.TestCase):
    def test_timer_list_goes_to_the_app_and_one_can_be_cancelled(self):
        from ai_linux.engine.service import Engine
        from ai_linux.engine.timers import Timers
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        events = []
        e = Engine(events.append, {})
        e.timers = Timers(Path(tmp.name) / "t.json", lambda m: None, on_change=e._timers_changed)
        self.addCleanup(e.timers.close)
        item = e.timers.add(300, "stretch")
        sent = [ev for ev in events if ev.get("ev") == "timers"]
        self.assertEqual([it["message"] for it in sent[-1]["items"]], ["stretch"])
        e.cancel_timer(item["id"])
        self.assertEqual([ev for ev in events if ev.get("ev") == "timers"][-1]["items"], [])
        e.cancel_timer("nope")  # unknown or already rung: nothing happens

class DictationEngineTests(unittest.TestCase):
    def make(self, armed=True, **settings):
        from ai_linux.engine.service import Engine
        events = []
        e = Engine(events.append, {"listen_mode": "wake", "wake_word": "phoenix", **settings})
        e.asr = mock.Mock()
        e.brain = mock.Mock()
        e.brain.env.executor.gate = ActionGate(armed=armed)
        e.brain.polish.side_effect = lambda text, cancel=None: text  # the big model's rewrite, faked
        e.cue = mock.Mock()
        return e, events

    def say(self, e, heard, at=None):
        e.asr.transcribe.return_value = heard
        e._one("audio", object(), at or time.monotonic())

    def dictating_by_hotkey(self, mode):
        e, events = self.make(listen_mode=mode)
        e.audio = mock.Mock()
        e.state = "idle"
        e.listen_now()
        self.say(e, "dictate")
        self.assertTrue(e.dictating)
        self.assertTrue(e.capturing)
        return e

    def test_the_name_inside_dictated_text_is_kept(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "The city of Phoenix is hot.")
            self.say(e, "We flew to Phoenix.")
            self.say(e, "Stop dictating.")
        self.assertEqual([c.args[1] for c in paste.call_args_list],
                         ["The city of Phoenix is hot. We flew to Phoenix. "])

    def test_only_the_name_while_dictating_is_not_typed(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "Buy milk.")
            self.say(e, "Phoenix.")
            self.say(e, "Stop dictating.")
        self.assertEqual(paste.call_args.args[1], "Buy milk. ")

    def test_start_words_during_dictation_are_text(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "Dictate a letter to John.")
            self.say(e, "Type exactly as the form says.")
            self.say(e, "Stop dictating.")
        self.assertEqual(e.brain.polish.call_args.args[0], "Dictate a letter to John. Type exactly as the form says.")
        self.assertEqual(paste.call_args.args[1], "Dictate a letter to John. Type exactly as the form says. ")

    def test_a_line_break_at_the_end_is_typed_without_a_stray_space(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "Hello there new paragraph")
            self.say(e, "Stop dictating.")
        self.assertEqual(paste.call_args.args[1], "Hello there\n\n")

    def test_cancel_while_the_model_works_types_nothing(self):
        e, events = self.make()
        seen = {}

        def polish(text, cancel=None):
            seen["cancel"] = cancel
            e.cancel_turn()  # pressed during "Fixing the wording"
            return text
        e.brain.polish.side_effect = polish
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "My secret words.")
            self.say(e, "Stop dictating.")
        paste.assert_not_called()
        self.assertTrue(seen["cancel"].is_set())  # the model stops generating too
        self.assertEqual(e.state, "idle")

    def _hotkey_during_the_wording_fix(self, press):
        e, events = self.make(listen_mode="hotkey", start_ping=False)
        e.audio = mock.Mock()
        e.state = "idle"
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        pressed = []

        def polish(text, cancel=None):
            press(e)
            pressed.append(time.monotonic())
            return text
        e.brain.polish.side_effect = polish
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            e.listen_now()
            self.say(e, "dictate")
            self.say(e, "Buy milk.")
            self.say(e, "Stop dictating.")
        return e, paste, pressed[0]

    def test_the_hotkey_during_the_wording_fix_keeps_the_words(self):
        for press in (lambda e: e.listen_now(), lambda e: e.push_to_talk(True)):
            e, paste, _at = self._hotkey_during_the_wording_fix(press)
            self.assertEqual([c.args[1] for c in paste.call_args_list], ["Buy milk. "])
            self.assertEqual(e.state, "listening")
            self.assertTrue(e.capturing)

    def test_speaking_after_the_hotkey_during_the_wording_fix_is_a_request(self):
        e, _paste, at = self._hotkey_during_the_wording_fix(lambda e: e.listen_now())
        self.say(e, "what time is it", at=at)
        e.brain.handle.assert_called_once()

    def test_speech_begun_while_dictating_is_never_a_request(self):
        e, events = self.make(listen_mode="always")
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        self.say(e, "Dictate.")
        began = time.monotonic() - 1  # the sentence was under way when Cancel came
        e.cancel_turn()
        self.say(e, "delete the downloads folder", at=began)
        e.brain.handle.assert_not_called()
        self.assertTrue([ev for ev in events if ev.get("ev") == "notice"])

    def test_speech_after_cancelling_dictation_is_heard_as_usual(self):
        e, events = self.make(listen_mode="always")
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        self.say(e, "Dictate.")
        e.cancel_turn()
        self.say(e, "what time is it")
        e.brain.handle.assert_called_once()

    def test_cancel_after_a_reply_needs_the_name_again(self):
        e, events = self.make()
        e.audio = mock.Mock()
        e.followup_until = time.monotonic() + 10
        e.set_state("followup")
        e.cancel_turn()
        self.assertEqual(e.state, "idle")
        self.assertFalse(e._wake_free())

    def test_polish_hands_the_words_over_as_a_transcript_not_a_request(self):
        b = brain_mod.Brain({"escalate_threshold": 0.85}.get, T.Env(None), speak=lambda s: None)
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "<transcript>Hi.</transcript>")])) as chat:
            self.assertEqual(b.polish("hi"), "Hi.")  # a small model can echo the tags
        msgs = chat.call_args.args[2]
        self.assertEqual(msgs[1]["content"], "<transcript>\nhi\n</transcript>")
        self.assertIn("never talking to you", msgs[0]["content"])

    def test_polish_hands_cancel_to_the_model_call(self):
        b = brain_mod.Brain({"escalate_threshold": 0.85}.get, T.Env(None), speak=lambda s: None)
        ev = threading.Event()
        with mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Hi.")])) as chat:
            self.assertEqual(b.polish("hi", ev), "Hi.")
        self.assertIs(chat.call_args.kwargs["cancel"], ev)

    def test_cancel_closes_the_mic_the_hotkey_opened(self):
        for mode, state in (("hotkey", "idle"), ("off", "muted")):
            e = self.dictating_by_hotkey(mode)
            e.cancel_turn()
            self.assertFalse(e.capturing, mode)
            self.assertEqual(e.state, state)

    def test_only_filler_after_the_hotkey_closes_the_mic(self):
        for mode, state in (("hotkey", "idle"), ("off", "muted")):
            e, events = self.make(listen_mode=mode)
            e.audio = mock.Mock()
            e.state = "idle"
            e.listen_now()
            self.say(e, "Um.")
            self.assertFalse(e.capturing, mode)
            self.assertEqual(e.state, state)

    def test_the_hotkey_running_out_during_a_typed_request_keeps_the_state(self):
        e, events = self.make(listen_mode="off")
        e.audio = mock.Mock()
        e.state = "idle"
        e.listen_now()
        e.state = "thinking"  # a request typed on Home, answered while the hotkey's wait ran out
        e._end_listen_now()
        self.assertEqual(e.state, "thinking")

    def test_the_end_of_a_reply_closes_the_mic_in_hotkey_mode(self):
        e, events = self.make(listen_mode="hotkey")
        e.audio = mock.Mock()
        e.state = "idle"
        e.listen_now()
        e.state = "thinking"  # a typed request: nothing closed the mic the hotkey opened
        threading.Thread(target=e._speak_loop, daemon=True).start()
        self.addCleanup(lambda: (e._stop.set(), e.say_q.put(("say", "", ""))))
        e.say_q.put(("end", "t", threading.Event()))
        deadline = time.monotonic() + 3
        while e.capturing:
            self.assertLess(time.monotonic(), deadline, "the mic stayed open")
            time.sleep(0.005)
        self.assertEqual(e.state, "idle")

    def test_refused_dictation_closes_the_mic(self):
        e, events = self.make(armed=False, listen_mode="hotkey")
        e.audio = mock.Mock()
        e.state = "idle"
        e.listen_now()
        self.say(e, "dictate")
        self.assertFalse(e.dictating)
        self.assertFalse(e.capturing)

    def test_a_turn_that_fails_closes_the_mic(self):
        e, events = self.make(listen_mode="off")
        e.audio = mock.Mock()
        e.state = "idle"
        e.listen_now()
        e.asr.transcribe.return_value = "what time is it"
        e.brain.handle.side_effect = RuntimeError("boom")
        threading.Thread(target=e._work_loop, daemon=True).start()
        self.addCleanup(lambda: (e._stop.set(), e.work.put(("text", "", 0.0))))
        e.work.put(("audio", object(), time.monotonic()))
        deadline = time.monotonic() + 3
        while e.capturing:
            self.assertLess(time.monotonic(), deadline, "the mic stayed open")
            time.sleep(0.005)
        self.assertEqual(e.state, "muted")

    def test_cancel_in_wake_mode_leaves_the_mic_listening(self):
        e, events = self.make()
        e.audio = mock.Mock()
        e._capture(True)
        self.say(e, "Phoenix, dictate.")
        e.cancel_turn()
        self.assertTrue(e.capturing)
        self.assertEqual(e.state, "idle")

    def test_after_cancelling_dictation_the_name_is_needed_again(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")):
            self.say(e, "Phoenix, dictate.")
            e.cancel_turn()
            self.say(e, "Open the files.")
        e.brain.handle.assert_not_called()

    def test_interrupting_a_reply_keeps_the_mic_open(self):
        e, events = self.make(listen_mode="hotkey")
        e.audio = mock.Mock()
        e.state = "speaking"
        e._capture(True)
        e.speaking = True
        e.listen_now()
        e.audio.stop_listening.assert_not_called()
        self.assertTrue(e.capturing)

    def test_always_on_room_speech_does_not_start_dictation(self):
        e, events = self.make(listen_mode="always", addressed_check=True)
        e.brain.addressed.return_value = False
        self.say(e, "Dictate the report by Friday.")
        self.assertFalse(e.dictating)

    def test_always_on_speech_for_it_starts_dictation(self):
        e, events = self.make(listen_mode="always", addressed_check=True)
        e.brain.addressed.return_value = True
        self.say(e, "Dictate the report by Friday.")
        self.assertEqual(e.dictating, "polish")

    def test_a_long_pause_to_think_does_not_end_it(self):
        from ai_linux.engine.service import DICTATE_IDLE_S
        self.assertGreaterEqual(DICTATE_IDLE_S, 120)  # 2026-10-31: only "stop dictating" should normally end it

    def test_the_safety_limit_says_it_ended(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "See you soon.")
            e.followup_until = time.monotonic() - 1
            e._check_followup()
            e._one(*e.work.get())
        paste.assert_called_once()
        self.assertTrue(any(ev.get("ev") == "notice" and "silence" in ev["text"] for ev in events), events)

    def test_stopping_by_voice_gives_no_notice(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")):
            self.say(e, "Phoenix, dictate.")
            self.say(e, "See you soon. Stop dictating.")
        self.assertFalse(any(ev.get("ev") == "notice" for ev in events), events)

    def test_speech_after_the_silence_ran_out_keeps_it_going(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "One more thing.")  # transcribed after the end was queued: the window is open again
            e._one("dictated", "sleep", 0.0)
        paste.assert_not_called()
        self.assertTrue(e.dictating)

    def test_a_timer_during_dictation_does_not_strand_it(self):
        e, events = self.make()
        e._play = mock.Mock(side_effect=lambda *a, **k: e.set_state("speaking"))
        threading.Thread(target=e._speak_loop, daemon=True).start()
        self.addCleanup(lambda: (e._stop.set(), e.say_q.put(("say", "", ""))))
        self.say(e, "Phoenix, dictate.")
        self.say(e, "Buy milk.")
        e.say_q.put(("ring", "Time to stretch."))
        deadline = time.monotonic() + 2
        while not e._play.called or e.state == "speaking":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        e.followup_until = time.monotonic() - 1
        e._check_followup()
        self.assertEqual(e.work.qsize(), 1)

    def test_live_words_while_dictating_keep_the_name(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")):
            self.say(e, "Phoenix, dictate.")
        e._live_open, e._utt = True, 1
        e._on_live(1, "The city of Phoenix is hot")
        self.assertEqual([ev["text"] for ev in events if ev.get("ev") == "partial"][-1], "The city of Phoenix is hot")

    def test_live_words_keep_coming_when_a_sentence_outlasts_the_window(self):
        for settings in ({"listen_mode": "wake"}, {"listen_mode": "always", "addressed_check": True}):
            e, events = self.make(**settings)
            self.say(e, "Phoenix, dictate.")
            e.followup_until = time.monotonic() - 1  # the sentence began in time and goes on
            e._live_open, e._utt = True, 1
            e._on_live(1, "The city of Phoenix is hot")
            self.assertEqual([ev["text"] for ev in events if ev.get("ev") == "partial"][-1],
                             "The city of Phoenix is hot", settings)

    def test_dictation_collects_until_stop_and_types_once(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, start dictating.")
            self.assertTrue(e.dictating)
            self.say(e, "Um, hello Sam comma new line see you at five full stop")
            self.say(e, "Done.")  # part of the text, not the end
            paste.assert_not_called()  # a pause to think types nothing yet
            self.say(e, "Phoenix, stop dictating.")
        self.assertEqual([c.args[1] for c in paste.call_args_list], ["Hello Sam,\nSee you at five. Done. "])
        e.brain.handle.assert_not_called()
        self.assertFalse(e.dictating)
        self.assertEqual(e.state, "idle")

    def test_dictate_sends_the_whole_text_to_the_big_model_once(self):
        e, events = self.make()
        e.brain.polish.side_effect = lambda text, cancel=None: "I went to the shop and bought milk. It was cold."
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "I has went to the shop and buyed milk.")
            self.say(e, "It were cold. Stop dictating.")
        e.brain.polish.assert_called_once()
        self.assertEqual(e.brain.polish.call_args.args[0], "I has went to the shop and buyed milk. It were cold.")
        self.assertEqual(paste.call_args.args[1], "I went to the shop and bought milk. It was cold. ")

    def test_type_what_i_say_types_it_as_said(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, type what I say.")
            self.say(e, "I has went to the shop.")
            self.say(e, "Stop dictating.")
        e.brain.polish.assert_not_called()
        self.assertEqual(paste.call_args.args[1], "I has went to the shop. ")

    def test_without_the_big_model_it_types_what_was_said(self):
        from ai_linux.engine import llm
        e, events = self.make()
        e.brain.polish.side_effect = llm.LLMError("connection refused")
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "I has went to the shop.")
            self.say(e, "Stop dictating.")
        self.assertEqual(paste.call_args.args[1], "I has went to the shop. ")
        self.assertTrue(any(ev.get("ev") == "notice" for ev in events))

    def test_text_after_the_start_word_is_kept(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate meeting moved to Monday.")
            self.say(e, "Stop dictating.")
        self.assertEqual(paste.call_args.args[1], "Meeting moved to Monday. ")

    def test_silence_types_what_was_said_off_the_listen_thread(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "See you soon.")
            e.followup_until = time.monotonic() - 1
            e._check_followup()  # the Listen thread: no model call here
            e._check_followup()  # once only
            e.brain.polish.assert_not_called()
            self.assertEqual(e.work.qsize(), 1)
            e._one(*e.work.get())  # the Work thread
        e.brain.polish.assert_called_once()
        self.assertEqual(e.brain.polish.call_args.args[0], "See you soon.")
        self.assertEqual(paste.call_args.args[1], "See you soon. ")
        self.assertFalse(e.dictating)
        self.assertEqual(e.state, "idle")

    def test_cancel_drops_what_was_collected(self):
        e, events = self.make()
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")) as paste:
            self.say(e, "Phoenix, dictate.")
            self.say(e, "Secret words.")
            e.cancel_turn()
            self.say(e, "Phoenix, dictate.")
            self.say(e, "Stop dictating.")
        paste.assert_not_called()

    def test_dictation_waits_longer_for_a_pause(self):
        from ai_linux.engine import service
        self.assertGreater(service.DICTATE_END_SILENCE, service.END_SILENCE)

    def test_needs_actions_allowed(self):
        e, events = self.make(armed=False)
        with mock.patch("ai_linux.engine.tools.paste_text") as paste:
            self.say(e, "Phoenix, dictate.")
        paste.assert_not_called()
        self.assertFalse(e.dictating)
        self.assertTrue(any("Safety" in ev.get("text", "") for ev in events if ev.get("ev") == "notice"))

class NameTestEngineTests(unittest.TestCase):
    def make(self):
        from ai_linux.engine.service import Engine
        events = []
        e = Engine(events.append, {"listen_mode": "hotkey"})
        e.asr = mock.Mock(transcribe=mock.Mock(return_value="Hello Phoenix."))
        e.brain = mock.Mock()
        e._capture = mock.Mock()
        e._ping = mock.Mock()
        return e, events

    def test_sentence_is_sent_back_not_acted_on(self):
        e, events = self.make()
        e.name_test_listen()
        e._one("audio", object(), 0.0)
        self.assertIn({"ev": "heard", "text": "Hello Phoenix."}, events)
        e.brain.handle.assert_not_called()
        self.assertEqual(e.name_test_until, 0.0)
        e._capture.assert_called_with(False)  # hotkey mode: the mic closes again

    def test_bare_wake_word_waits_for_the_request(self):
        e, events = self.make()
        e.s["wake_word"] = "phoenix"
        e.asr.transcribe.return_value = "Phoenix."
        e.armed_until = time.monotonic() + 5  # wake-free window, as after the hotkey
        e._one("audio", object(), 0.0)
        e.brain.handle.assert_not_called()
        self.assertEqual(e.state, "followup")

    def test_listen_while_starting_says_so(self):
        e, events = self.make()
        e.state = "loading"
        e.listen_now()
        self.assertTrue(any(ev.get("ev") == "notice" for ev in events))
        e._capture.assert_not_called()

    def test_new_session_forgets_the_conversation(self):
        e, events = self.make()
        e.new_session()
        e.brain.new_session.assert_called_once()

    def test_expired_test_listens_normally(self):
        e, events = self.make()
        e.name_test_listen()
        e.name_test_until = e.armed_until = 1.0  # both long past: back to needing the wake word
        e.asr.transcribe.return_value = "open my files"
        e._one("audio", object(), 0.0)
        self.assertFalse(any(ev.get("ev") == "heard" for ev in events))

    def test_speech_without_wake_word_sends_a_hint(self):
        e, events = self.make()
        e.s.update(listen_mode="wake", wake_word="phoenix")
        e.asr.transcribe.return_value = "Hello there."
        e._one("audio", object(), 0.0)
        self.assertIn({"ev": "no_wake"}, events)
        e.brain.handle.assert_not_called()

    def test_noise_without_wake_word_stays_quiet(self):
        e, events = self.make()
        e.s.update(listen_mode="wake", wake_word="phoenix")
        for heard in ("", "Um.", "okay"):
            e.asr.transcribe.return_value = heard
            e._one("audio", object(), 0.0)
        self.assertNotIn({"ev": "no_wake"}, events)

    def test_sleep_closes_the_follow_up_window(self):
        e, events = self.make()
        e.s["listen_mode"] = "wake"
        e._play = mock.Mock()
        e._sleep()  # "go to sleep" ran during the turn
        e.say_q.put(("end", "t"))
        threading.Thread(target=e._speak_loop, daemon=True).start()
        deadline = time.monotonic() + 2
        while e.sleep_after and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual((e.state, e.followup_until), ("idle", 0.0))  # no follow-up window after sleeping
        e._stop.set()
        e.say_q.put(("say", "", ""))

    def test_called_then_silence_goes_back_to_sleep(self):
        e, events = self.make()
        e.cue = mock.Mock()
        e._called()
        self.assertEqual(e.state, "followup")
        e._check_followup()
        e.cue.assert_not_called()  # still inside the window
        e.followup_until = time.monotonic() - 1
        e._check_followup()
        self.assertEqual(e.state, "idle")
        e.cue.assert_called_once_with("sleep")

    def test_called_then_no_words_sounds_unclear(self):
        e, events = self.make()
        e.s.update(listen_mode="wake", wake_word="phoenix")
        e.cue = mock.Mock()
        e._called()
        e.asr.transcribe.return_value = "um"
        e._one("audio", object(), 0.0)
        e.cue.assert_called_once_with("unclear")
        e.brain.handle.assert_not_called()

    def test_confirm_shows_the_whole_command(self):
        e, events = self.make()
        long_cmd = "find ~/Downloads -name '*.tmp' -mtime +30 -print"
        e._say("Should I run this command? Say yes or no.", f"Run this? {long_cmd} Say yes or no.")
        kind, spoken, shown = e.say_q.get_nowait()[:3]
        self.assertNotIn(long_cmd, spoken)
        self.assertIn(long_cmd, shown)

    def test_speech_started_in_the_window_counts_after_it_closed(self):
        e, events = self.make()
        e.s.update(listen_mode="wake", wake_word="phoenix")
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e.asr.transcribe.return_value = "what are you doing"
        started = time.monotonic()
        e.followup_until = started + 0.5
        e._one("audio", object(), started)  # judged at its start, not after the user finished talking
        e.brain.handle.assert_called_once()
        e.brain.handle.reset_mock()
        e._one("audio", object(), started + 1.0)  # began after the window: needs the name
        e.brain.handle.assert_not_called()

    def test_unclear_confirm_reply_becomes_the_next_request(self):
        from ai_linux.safety.gate import ConfirmRequest
        e, events = self.make()
        e.audio = mock.Mock()
        e.s.update(listen_mode="wake", wake_word="phoenix")
        e.brain.yes_no.return_value = None
        e.asr.transcribe.return_value = "Hello Phoenix, what are you doing?"
        e.work.put(("audio", object(), time.monotonic()))
        ev, box = threading.Event(), {"ok": False}
        e._confirm_by_voice(ConfirmRequest("shell.run_command", {"command": "ls"}, "ls"), ev, box)
        self.assertFalse(box["ok"])  # not run
        kind, text, _t = e.work.get_nowait()
        self.assertEqual((kind, text), ("heard", "Hello Phoenix, what are you doing?"))
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e._one(kind, text, _t)
        self.assertEqual(e.brain.handle.call_args[0][0], "what are you doing?")

    def test_mic_opens_even_if_a_timer_rang_while_starting(self):
        e, events = self.make()
        e.s["listen_mode"] = "wake"
        e.brain.warm.side_effect = RuntimeError("ollama hung up")
        e.asr.warm = mock.Mock()
        e.state = "idle"  # a missed timer rang during the start and left the state at idle
        e._get_ready()
        e._capture.assert_called_with(True)

    def test_the_waking_phrase_goes_back_to_thinking(self):
        e, events = self.make()
        e._play = mock.Mock()
        e._busy, e._think_detail = True, "Waking up the big model"
        e._say_wait("Waking up the big model.")
        threading.Thread(target=e._speak_loop, daemon=True).start()
        deadline = time.monotonic() + 2
        while e.state != "thinking" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(e.state, "thinking")
        self.assertIn({"ev": "state", "state": "thinking", "detail": "Waking up the big model"}, events)
        e._stop.set()
        e.say_q.put(("end", "x"))

    def test_interrupting_keeps_a_timer_ringing(self):
        e, events = self.make()
        e.say_q.put(("say", "a long reply", "a long reply"))
        e.say_q.put(("ring", "Reminder: tea."))
        e.cancel_turn()
        self.assertEqual(e.say_q.get_nowait()[0], "ring")

    def test_stop(self):
        e, _ = self.make()
        e.name_test_listen()
        e.name_test_stop()
        self.assertEqual((e.name_test_until, e.armed_until), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()


class BargeInTests(unittest.TestCase):
    def make(self, heard: str):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.asr = mock.Mock(transcribe=mock.Mock(return_value=heard))
        e.audio = mock.Mock()
        e._saying = "Droupadi Murmu is the current president of India."
        e._pause_reply()
        return e

    def test_echo_words_are_not_the_user(self):
        from ai_linux.engine.service import echo_of
        said = "Droupadi Murmu is the current president of India."
        self.assertTrue(echo_of("president of India", said))
        self.assertTrue(echo_of("", said))
        self.assertTrue(echo_of("Hmm.", said))
        self.assertFalse(echo_of("Stop.", said))
        self.assertFalse(echo_of("What is your name?", said))

    def test_own_echo_resumes_the_reply(self):
        e = self.make("the current president")
        e._barge_verdict(np.zeros(16000, dtype=np.float32))
        self.assertFalse(e._hold.is_set())
        self.assertTrue(e._held())
        self.assertFalse(e.cancel.is_set())
        self.assertTrue(e.work.empty())
        self.assertEqual(e._false_barges, 1)

    def test_short_word_from_the_user_stops_the_reply(self):
        e = self.make("Stop.")
        e._barge_verdict(np.zeros(16000, dtype=np.float32))
        self.assertTrue(e.cancel.is_set())
        self.assertFalse(e._hold.is_set())
        self.assertEqual(e.work.get_nowait(), ("heard", "Stop.", 0.0))

    def test_words_of_the_sentence_before_are_our_voice_too(self):
        e = self.make("I hear you")
        e._said_before = "I hear you are struggling."
        e._barge_verdict(np.zeros(16000, dtype=np.float32))
        self.assertFalse(e.cancel.is_set())
        self.assertTrue(e.work.empty())

    def test_a_voice_sample_the_app_plays_is_our_own_sound(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.audio = mock.Mock()
        stereo = mock.Mock(read=mock.Mock(return_value=(np.zeros((800, 2), np.float32), 16000)))
        with mock.patch.dict(sys.modules, {"soundfile": stereo}):
            e.playing_sample("sample.ogg")
        sound, rate = e.audio.hear_own.call_args.args
        self.assertEqual((sound.shape, rate), ((800,), 16000))
        e.playing_sample("")
        e.audio.stop_own.assert_called_once()

    def test_paused_sentence_is_said_again_after_an_echo(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.tts = mock.Mock(sample_rate=16000, generate_speech_audio=mock.Mock(return_value=np.ones(1600, np.float32) * 0.1))
        calls = []

        def measure(n, sr):
            calls.append(n)
            if len(calls) == 1:  # the first play is cut by a barge-in that turns out to be our echo
                e._pause_reply()
                threading.Timer(0.05, lambda: (setattr(e, "_hold_resume", True), e._hold.clear())).start()
                return True, 30
            return False, 100

        e.audio = mock.Mock(measure_percentage_spoken=measure)
        e._play("Hello there.")
        self.assertEqual(len(calls), 2)



@unittest.skipIf(importlib.util.find_spec("onnxruntime") is None, "the VAD needs the engine's onnxruntime")
class AudioIoTests(unittest.TestCase):
    """PipeWire is faked: no pw-record or pw-play runs."""

    def make(self, canceller=None):
        import contextlib
        from ai_linux.engine import audio
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        self.vad = stack.enter_context(mock.patch.object(audio, "VAD")).return_value
        self.vad.return_value = 0.5
        self.canceller = canceller if canceller is not None else mock.Mock(ok=True, clean=lambda f: f * 0)
        stack.enter_context(mock.patch.object(audio.echo, "load", return_value=self.canceller))
        self.popen = stack.enter_context(mock.patch.object(audio.subprocess, "Popen"))
        self.popen.return_value.poll.return_value = 0
        self.popen.return_value.returncode = 0
        self.popen.return_value.stdout.read.return_value = b""
        return audio.PipeWireAudioIO()

    def test_setting_up_changes_nothing_in_pipewire(self):
        self.make()
        self.popen.assert_not_called()  # was a held pw-cli with the echo-cancel module and a forced 480 quantum

    def test_the_mic_is_the_plain_default_source(self):
        io = self.make()
        io.start_listening()
        self.addCleanup(io.stop_listening)
        cmd = self.popen.call_args.args[0]
        self.assertEqual(cmd[0], "pw-record")
        self.assertNotIn("--target", cmd)

    def test_the_engine_can_stop_with_the_real_audio_class(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.audio = self.make()
        e.stop()  # 2026-10-31: raised AttributeError on a removed method, so quitting skipped os._exit

    def test_a_sound_under_way_when_the_mic_opens_is_the_reference(self):
        io = self.make()
        self.addCleanup(io.stop_listening)
        io.hear_own(np.zeros(16000, np.float32), 16000)
        self.canceller.feed.assert_not_called()
        io.start_listening()
        fed = self.canceller.feed.call_args.args[0]
        self.assertTrue(8000 < len(fed) <= 16000, len(fed))  # what is left of it, not all of it

    def test_a_sound_that_ended_before_the_mic_opened_is_not(self):
        io = self.make()
        self.addCleanup(io.stop_listening)
        io.hear_own(np.zeros(1600, np.float32), 16000)
        io.stop_own()
        io.start_listening()
        self.canceller.feed.assert_not_called()

    def test_opening_the_mic_forgets_any_sound_left_over(self):
        io = self.make()
        self.addCleanup(io.stop_listening)
        io.stop_own()  # a sound ended while the mic was closed, which sets the room's tail
        self.canceller.reset.reset_mock()
        io.start_listening()
        self.canceller.reset.assert_called()

    def test_voice_detection_sees_the_cleaned_frame(self):
        import io as stdio
        io = self.make()
        io._rec_proc = mock.Mock(stdout=stdio.BytesIO(np.full(512, 1000, np.int16).tobytes()))
        io._read_loop()
        frame, prob = io.get_sample_queue().get_nowait()
        self.assertEqual(float(np.abs(frame).max()), 0.0)
        self.assertEqual(float(np.abs(self.vad.call_args.args[0]).max()), 0.0)

    def test_the_reply_is_the_reference_from_before_it_plays_until_it_ends(self):
        order = []
        c = mock.Mock(ok=True)
        c.feed.side_effect = lambda *a: order.append("feed")
        c.stop.side_effect = lambda: order.append("stop")
        io = self.make(c)
        io._rec_proc = mock.Mock()  # the mic is open
        self.popen.side_effect = lambda *a, **k: (order.append("play"), self.popen.return_value)[1]
        audio_data = np.zeros(1600, np.float32)
        io.start_speaking(audio_data, 16000)
        io.measure_percentage_spoken(1600, 16000)
        self.assertEqual(order, ["feed", "play", "stop"])
        self.assertNotIn("--target", self.popen.call_args.args[0])

    def test_without_the_canceller_there_is_no_barge_in(self):
        from ai_linux.engine import audio
        with mock.patch.object(audio, "VAD"), mock.patch.object(audio.echo, "load", return_value=None):
            self.assertFalse(audio.PipeWireAudioIO()._aec_ok)

    def test_a_canceller_that_broke_ends_barge_in(self):
        c = mock.Mock(ok=False)
        self.assertFalse(self.make(c)._aec_ok)

    def test_own_sound_is_the_reference_only_while_the_mic_is_open(self):
        c = mock.Mock(ok=True)
        io = self.make(c)
        io.hear_own(np.zeros(100, np.float32), 24000)
        c.feed.assert_not_called()  # with no frames to pace it, it would still be there when the mic opens
        time.sleep(0.01)  # over by then
        io.start_listening()
        self.addCleanup(io.stop_listening)
        io.hear_own(np.zeros(100, np.float32), 24000)
        c.feed.assert_called_once()

    def test_closing_the_mic_forgets_the_reference(self):
        c = mock.Mock(ok=True)
        io = self.make(c)
        io.start_listening()
        io.stop_listening()
        c.reset.assert_called()


class McpCleanupTests(unittest.TestCase):
    SERVER = Path(__file__).with_name("fake_mcp_server.py")

    @staticmethod
    def slow_pair():
        both = threading.Barrier(2, timeout=1)
        late = []

        class Slow:
            def close(self):
                try:
                    both.wait()
                except threading.BrokenBarrierError:
                    late.append(self)
        return [Slow(), Slow()], late

    def test_quit_closes_every_server_at_once(self):
        from ai_linux.engine import mcp_client
        servers, late = self.slow_pair()
        with mock.patch.object(mcp_client, "_running", set(servers)):
            mcp_client.close_all()
        self.assertEqual(late, [])

    def test_closing_a_tool_set_closes_its_servers_at_once(self):
        from ai_linux.engine import mcp_client
        servers, late = self.slow_pair()
        tools = mcp_client.McpTools([])
        tools.servers = servers
        tools.close()
        self.assertEqual(late, [])

    def test_quit_closes_servers_while_the_model_unloads(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e.brain = mock.Mock(base="http://127.0.0.1:11434", big="qwen3.5:4b")
        both = threading.Barrier(2, timeout=1)
        e.mcp = mock.Mock(close=mock.Mock(side_effect=both.wait))
        with mock.patch("ai_linux.engine.llm.unload", side_effect=lambda *a, **k: both.wait()):
            e.stop()

    def test_server_whose_tool_list_fails_is_stopped(self):
        from ai_linux.engine import mcp_client
        made = []

        class Spy(mcp_client.McpServer):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                made.append(self)

            def list_tools(self):
                raise mcp_client.McpError("tools/list: no answer in 30 s")

        with mock.patch.object(mcp_client, "McpServer", Spy):
            tools = mcp_client.McpTools([{"name": "demo", "command": sys.executable, "args": [str(self.SERVER)]}])
        self.assertEqual(tools.tools(), [])
        try:
            made[0].proc.wait(timeout=3)
        except Exception:
            made[0].close()
            self.fail("the server was left running")

    def test_reloading_mcp_twice_at_once_closes_every_old_server(self):
        from ai_linux.engine import mcp_client
        from ai_linux.engine.service import Engine
        made = []

        class Fake:
            def __init__(self, specs, notice=None):
                time.sleep(0.2)  # servers take a while to start
                self.closed = False
                made.append(self)

            catalog: dict = {}

            def tools(self, *disabled):
                return []

            def close(self):
                self.closed = True

        e = Engine(lambda ev: None, {"mcp_servers": [{"name": "a", "command": "x"}]})
        e.brain = mock.Mock()
        with mock.patch.object(mcp_client, "McpTools", Fake):
            threads = [threading.Thread(target=e._load_mcp) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(3)
        self.assertEqual(len(made), 2)
        self.assertEqual([m for m in made if not m.closed], [e.mcp])

    def test_quit_while_mcp_servers_start_stops_them(self):
        from ai_linux.engine import mcp_client
        from ai_linux.engine.service import Engine
        made = []

        class Fake:
            def __init__(self, specs, notice=None):
                time.sleep(0.3)
                self.closed = False
                made.append(self)

            catalog: dict = {}

            def tools(self, *disabled):
                return []

            def close(self):
                self.closed = True

        e = Engine(lambda ev: None, {"mcp_servers": [{"name": "a", "command": "x"}]})
        e.brain = mock.Mock()
        with mock.patch.object(mcp_client, "McpTools", Fake), mock.patch("ai_linux.engine.llm.unload"):
            t = threading.Thread(target=e._load_mcp)
            t.start()
            time.sleep(0.05)
            e.stop()  # the app quits; servers live in their own session, so nothing else ends them
            t.join(2)
        self.assertTrue(made and made[0].closed)

    def test_quit_ends_a_server_that_has_not_answered_yet(self):
        import subprocess
        from ai_linux.engine import mcp_client
        from ai_linux.engine.service import Engine
        procs, real = [], subprocess.Popen

        def spy(*a, **k):
            procs.append(real(*a, **k))
            return procs[-1]
        spec = {"name": "slow", "command": sys.executable, "args": ["-c", "import time; time.sleep(30)"]}
        e = Engine(lambda ev: None, {"mcp_servers": [spec]})
        e.brain = mock.Mock()
        with mock.patch.object(mcp_client.subprocess, "Popen", spy), mock.patch("ai_linux.engine.llm.unload"):
            threading.Thread(target=e._load_mcp, daemon=True).start()
            deadline = time.monotonic() + 3
            while not procs:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            self.addCleanup(procs[0].kill)
            e.stop()  # __main__ calls os._exit right after this: the loading thread gets no further
            self.assertIsNotNone(procs[0].poll(), "still running when stop() returned")

    def test_a_server_that_answers_after_close_is_stopped(self):
        from ai_linux.engine import mcp_client
        release, made = threading.Event(), []

        class Late:
            def __init__(self, spec):
                self.name, self.closed = spec["name"], False
                made.append(self)
                release.wait(5)

            def list_tools(self):
                return []

            def close(self):
                self.closed = True

        with mock.patch.object(mcp_client, "McpServer", Late), mock.patch.object(mcp_client, "START_S", -14.9):
            tools = mcp_client.McpTools([{"name": "late", "command": "x"}])  # gave up waiting after 0.1 s
        tools.close()  # settings changed: the servers are reloaded
        release.set()
        deadline = time.monotonic() + 3
        while not made[0].closed:
            self.assertLess(time.monotonic(), deadline, "left running")
            time.sleep(0.01)


class TurnOverlapTests(unittest.TestCase):
    """A cancelled turn's leftovers must not reach the next turn (the speaker runs on its own thread)."""

    def make(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {"listen_mode": "wake"})
        e._play = mock.Mock()
        e.brain = mock.Mock()
        return e

    def start_speaker(self, e):
        threading.Thread(target=e._speak_loop, daemon=True).start()
        self.addCleanup(lambda: (e._stop.set(), e.say_q.put(("say", "", ""))))

    @staticmethod
    def drain(e):
        deadline = time.monotonic() + 2
        while not e.say_q.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)

    def cancelled_first_turn(self, e):
        def handle(text, source, cancel):
            cancel.set()  # the user cancelled; this sentence was already on its way past the cleared queue
            e._say("the rest of the old answer")
            return {"id": "a", "latency": {}}
        e.brain.handle.side_effect = handle
        e._one("text", "first", 0.0)

    def test_late_sentence_of_a_cancelled_turn_is_not_spoken_in_the_next(self):
        e = self.make()
        self.cancelled_first_turn(e)
        e.brain.handle.side_effect = lambda text, source, cancel: (e._say("the new answer"),
                                                                  {"id": "b", "latency": {}})[1]
        e._one("text", "second", 0.0)
        self.start_speaker(e)
        self.drain(e)
        self.assertEqual([c.args[0] for c in e._play.call_args_list], ["the new answer"])

    def test_end_of_a_cancelled_turn_leaves_the_next_one_thinking(self):
        e = self.make()
        self.cancelled_first_turn(e)
        seen = {}

        def handle(text, source, cancel):
            self.start_speaker(e)
            self.drain(e)
            seen["state"] = e.state
            return {"id": "b", "latency": {}}
        e.brain.handle.side_effect = handle
        e._one("text", "second", 0.0)
        self.assertEqual(seen["state"], "thinking")


class EngineReviewTests(unittest.TestCase):
    def test_cancel_ends_a_confirmation_waiting_on_screen(self):
        from ai_linux.engine.service import Engine
        from ai_linux.safety.gate import ConfirmRequest
        events = []
        e = Engine(events.append, {"spoken_confirm": False})
        out = {}
        req = ConfirmRequest("shell.run_command", {"command": "ls"}, "ls")
        t = threading.Thread(target=lambda: out.setdefault("ok", e._confirm(req)), name="Work", daemon=True)
        t.start()
        deadline = time.monotonic() + 2
        while not any(ev.get("ev") == "confirm" for ev in events) and time.monotonic() < deadline:
            time.sleep(0.01)
        e.cancel_turn()
        t.join(2)
        self.assertFalse(t.is_alive())  # was stuck for CONFIRM_S, and every request after it waited too
        self.assertFalse(out["ok"])
        self.assertTrue(any(ev.get("ev") == "confirm_done" for ev in events))  # the dialog closes

    def test_listen_mode_change_while_starting_waits_for_start_up(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {"listen_mode": "wake"})
        e.audio, e._capture = mock.Mock(), mock.Mock()
        e.state = "loading"
        e.update_settings({"listen_mode": "always"})
        self.assertEqual(e.state, "loading")  # _get_ready opens the mic once the models are warm
        e._capture.assert_not_called()


class ListenLoopStateTests(unittest.TestCase):
    """The real listen loop, fed as the mic feeds it: a steady stream of frames, so the queue is never empty."""

    def make(self, mode="wake"):
        import queue
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {"listen_mode": mode, "wake_word": "phoenix", "start_ping": False})
        e.asr, e.brain, e.cue = mock.Mock(), mock.Mock(), mock.Mock()
        e.brain.env.executor.gate = ActionGate(armed=True)
        frames = queue.Queue()
        e.audio = mock.Mock(get_sample_queue=mock.Mock(return_value=frames), _aec_ok=False)
        e.state = "idle"
        e._capture(True)
        self.voiced = 0
        done = threading.Event()
        frame = np.full(512, 0.01, np.float32)

        def feed():
            while not done.is_set():
                prob = 0.9 if self.voiced > 0 else 0.0
                self.voiced = max(0, self.voiced - 1)
                frames.put((frame, prob))
                time.sleep(0.002)
        threading.Thread(target=feed, daemon=True).start()
        threading.Thread(target=e._listen_loop, daemon=True).start()
        self.addCleanup(lambda: (done.set(), e._stop.set()))
        return e

    def say(self, e, heard):
        e.asr.transcribe.return_value = heard
        e._one("audio", object(), time.monotonic())

    def until(self, check, what):
        deadline = time.monotonic() + 3
        while not check():
            self.assertLess(time.monotonic(), deadline, what)
            time.sleep(0.005)

    def barge(self, e, heard):
        """Our sentence is playing and voice comes in, long enough that it isn't a short noise."""
        e.audio._aec_ok = True
        e.asr.transcribe.return_value = heard
        e._saying = "I hear you are struggling to breathe."
        e.speaking = True
        e.set_state("speaking")
        self.voiced = 30
        self.until(lambda: e._hold.is_set() or e.cancel.is_set(), "no barge-in")

    def test_a_long_stretch_of_our_own_voice_is_not_a_request(self):
        e = self.make()
        self.barge(e, "I hear you")  # 2026-10-31: this became a request and the model answered itself
        self.until(lambda: not e._hold.is_set(), "no verdict")
        self.assertTrue(e._hold_resume)
        self.assertFalse(e.cancel.is_set())
        self.assertTrue(e.work.empty())

    def test_a_long_interruption_with_new_words_stops_the_reply(self):
        e = self.make()
        self.barge(e, "What is the weather in Paris")
        self.until(lambda: e.work.qsize() == 1, "no request")
        self.assertEqual(e.work.get_nowait(), ("heard", "What is the weather in Paris", 0.0))
        self.assertTrue(e.cancel.is_set())

    def test_the_pill_shows_listening_during_a_long_interruption(self):
        e = self.make()
        self.barge(e, "What is the weather in Paris")
        self.until(lambda: e.state == "listening", "stayed on speaking")

    def test_our_last_words_heard_just_after_the_reply_are_dropped(self):
        from ai_linux.engine.service import FRAME_S, PREROLL
        e = self.make("always")
        e._saying, e._spoke_until = "I can browse the web for you.", time.monotonic()
        e.asr.transcribe.return_value = "browse the web"
        e._one("audio", object(), time.monotonic() - PREROLL * FRAME_S)
        e.brain.handle.assert_not_called()

    def test_a_request_with_new_words_just_after_the_reply_is_taken(self):
        from ai_linux.engine.service import FRAME_S, PREROLL
        e = self.make("always")
        e.brain.addressed.return_value = True
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e._saying, e._spoke_until = "I can browse the web for you.", time.monotonic()
        e.asr.transcribe.return_value = "open the calculator please"
        e._one("audio", object(), time.monotonic() - PREROLL * FRAME_S)
        e.brain.handle.assert_called_once()

    def test_the_same_words_said_later_are_a_request(self):
        from ai_linux.engine.service import ECHO_TAIL_S
        e = self.make("always")
        e.brain.addressed.return_value = True
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e._saying, e._spoke_until = "I can browse the web for you.", time.monotonic() - 2 * ECHO_TAIL_S - 2
        e.asr.transcribe.return_value = "browse the web"
        e._one("audio", object(), time.monotonic())
        e.brain.handle.assert_called_once()

    def test_a_quick_name_is_not_too_short(self):
        e = self.make()
        self.voiced = 3  # 2026-10-31: "Phoenix" scored 3, 6 and 7, and was thrown away after lighting up
        self.until(lambda: e.work.qsize() == 1, "dropped")

    def test_the_shortest_name_heard_is_not_too_short(self):
        e = self.make()
        self.voiced = 2
        self.until(lambda: e.work.qsize() == 1, "dropped")

    def test_a_single_click_while_waiting_for_the_name_is_not_transcribed(self):
        e = self.make()
        self.voiced = 1
        time.sleep(0.2)
        self.assertTrue(e.work.empty())

    def test_a_reply_with_listening_off_goes_back_to_muted(self):
        e = self.make("off")
        threading.Thread(target=e._speak_loop, daemon=True).start()
        e.set_state("speaking")
        e.say_q.put(("end", "t", threading.Event()))
        self.until(lambda: e.state == "muted", f"stayed on {e.state}")  # the closed mic can't end a window

    def test_a_cough_in_a_window_is_still_too_short(self):
        e = self.make()
        e.followup_until = time.monotonic() + 5
        e.set_state("followup")
        self.voiced = 5  # any words here would go to the model, with no name to filter them
        self.until(lambda: e.state == "listening", "the cough opened a recording")
        self.until(lambda: e.state != "listening", "stayed on listening")
        self.assertTrue(e.work.empty())

    def test_a_cough_while_always_on_is_still_too_short(self):
        e = self.make("always")
        self.voiced = 5
        self.until(lambda: e.state == "listening", "the cough opened a recording")
        self.until(lambda: e.state != "listening", "stayed on listening")
        self.assertTrue(e.work.empty())

    def test_talking_over_a_timer_while_dictating_stays_dictation(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        e.audio._aec_ok, window = True, e.followup_until
        e.speaking = True  # a timer ringing
        self.voiced = 30
        self.until(lambda: e.work.qsize() == 1, "not recorded")
        self.assertFalse(e._hold.is_set())
        self.assertFalse(e.cancel.is_set())
        self.assertEqual((e.dictating, e.followup_until), ("polish", window))

    def test_a_long_dictated_stretch_is_cut_at_a_short_pause(self):
        from ai_linux.engine import service
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        with mock.patch.object(service, "DICTATE_CUT_FRAMES", 40):
            self.voiced = 60
            self.until(lambda: e.work.qsize() == 1, "still recording")
        self.assertLess(service.DICTATE_CUT_FRAMES + 8, service.MAX_FRAMES)
        self.assertLess(len(e.work.get_nowait()[1]) / 512, 60 + service.DICTATE_END_SILENCE)  # no 1.5 s wait

    def test_a_short_pause_early_in_dictation_does_not_cut_it(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        self.voiced = 20  # far under the cut: only the ~1.5 s silence ends this one
        self.until(lambda: e.work.qsize() == 1, "still recording")
        from ai_linux.engine import service
        self.assertGreaterEqual(len(e.work.get_nowait()[1]) / 512, 20 + service.DICTATE_END_SILENCE)

    def test_words_from_the_reply_said_with_the_hotkey_are_a_request(self):
        from ai_linux.engine.service import FRAME_S, PREROLL, PTT_END_S
        e = self.make("hotkey")
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e._saying, e._spoke_until = "Should I turn off the lights?", time.monotonic()
        e.armed_until = time.monotonic() + PTT_END_S  # hold-to-talk just released
        e.asr.transcribe.return_value = "turn off the lights"
        e._one("audio", object(), time.monotonic() - PREROLL * FRAME_S)
        e.brain.handle.assert_called_once()

    def test_typing_stop_dictating_ends_it(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        with mock.patch("ai_linux.engine.tools.paste_text", return_value=T.Result(True, "")):
            e._one("text", "Stop dictating.", 0.0)
        self.assertFalse(e.dictating)
        e.brain.handle.assert_not_called()

    def test_a_cough_in_a_dictation_pause_does_not_strand_it(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        self.say(e, "Buy milk.")
        e.followup_until = time.monotonic() + 0.4
        self.voiced = 5  # under MIN_SPEECH
        self.until(lambda: e.state == "listening", "the cough opened a recording")
        self.until(lambda: e.work.qsize() == 1, "the silence end never came")

    def test_a_cough_that_outlasts_the_dictation_window_does_not_strand_it(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        self.say(e, "Buy milk.")
        e.followup_until = time.monotonic() + 0.03
        self.voiced = 5
        self.until(lambda: e.work.qsize() == 1, "the silence end never came")

    def test_a_cough_in_a_follow_up_window_ends_in_idle(self):
        e = self.make()
        e.followup_until = time.monotonic() + 0.4
        e.set_state("followup")
        self.voiced = 5
        self.until(lambda: e.state == "listening", "the cough opened a recording")
        self.until(lambda: e.state == "idle", "stayed on listening")

    def test_a_cough_while_always_on_ends_in_idle(self):
        e = self.make("always")
        self.voiced = 5
        self.until(lambda: e.state == "listening", "the cough opened a recording")
        self.until(lambda: e.state == "idle", "stayed on listening")

    def test_nothing_said_after_the_hotkey_ends_listening(self):
        from ai_linux.engine import service
        for mode, state, mic in (("hotkey", "idle", False), ("off", "muted", False), ("wake", "idle", True)):
            e = self.make(mode)
            with mock.patch.object(service, "LISTEN_NOW_S", 0.1):
                e.listen_now()
            self.until(lambda: e.state == state, f"{mode}: stayed on {e.state}")
            self.until(lambda: e.capturing == mic, f"{mode}: mic")

    def test_a_request_being_transcribed_is_not_nothing_said(self):
        from ai_linux.engine import service
        e = self.make("hotkey")
        with mock.patch.object(service, "LISTEN_NOW_S", 0.05):
            e.listen_now()
            self.voiced = 100  # still talking when the 0.05 s are up
        self.until(lambda: e.work.qsize() == 1, "no recording")
        time.sleep(0.05)
        self.assertTrue(e.capturing)

    def test_the_name_test_waits_its_whole_time(self):
        from ai_linux.engine import service
        e = self.make("hotkey")
        with mock.patch.object(service, "LISTEN_NOW_S", 0.05):
            e.name_test_listen()
            time.sleep(0.3)
        self.assertTrue(e.capturing)

    def test_the_end_of_a_follow_up_window_closes_the_mic_when_off(self):
        e = self.make("off")
        e.followup_until = time.monotonic() + 0.1
        e.set_state("followup")
        self.until(lambda: e.state == "muted", "stayed on followup")
        self.until(lambda: not e.capturing, "the mic stayed open")

    def test_a_short_tap_on_hold_to_talk_closes_the_mic(self):
        e = self.make("hotkey")
        e.push_to_talk(True)
        self.until(lambda: e.state == "listening", "no state")
        e.push_to_talk(False)
        self.until(lambda: not e.capturing, "the mic stayed open")
        self.assertEqual(e.state, "idle")

    def test_hold_to_talk_needs_no_name(self):
        e = self.make("hotkey")
        e.brain.handle.return_value = {"id": "t", "latency": {}}
        e.push_to_talk(True)
        started = time.monotonic()
        e.push_to_talk(False)
        e.asr.transcribe.return_value = "what time is it"
        e._one("audio", object(), started)
        e.brain.handle.assert_called_once()

    def test_dictation_ends_after_the_listen_mode_closed_the_mic(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        self.say(e, "Buy milk.")
        e.followup_until = time.monotonic() + 0.1
        e.s["listen_mode"] = "off"
        e.apply_listen_mode()
        self.until(lambda: e.work.qsize() == 1, "the words were stranded")

    def test_dictation_ends_when_the_mic_closes_mid_sentence(self):
        e = self.make()
        self.say(e, "Phoenix, dictate.")
        self.say(e, "Buy milk.")
        e.followup_until = time.monotonic() + 0.2
        self.voiced = 100000
        self.until(lambda: e.state == "listening", "no recording")
        e.s["listen_mode"] = "hotkey"
        e.apply_listen_mode()
        self.until(lambda: e.work.qsize() == 1, "the words were stranded")

    def test_the_mic_closing_mid_sentence_leaves_listening(self):
        e = self.make("always")
        self.voiced = 100000
        self.until(lambda: e.state == "listening", "no recording")
        e.s["listen_mode"] = "hotkey"
        e.apply_listen_mode()
        self.until(lambda: e.state == "idle", "stayed on listening with the mic closed")

    def ring_during(self, e):
        e._play = mock.Mock(side_effect=lambda *a, **k: e.set_state("speaking"))
        threading.Thread(target=e._speak_loop, daemon=True).start()
        self.addCleanup(lambda: (e._stop.set(), e.say_q.put(("say", "", ""))))
        e.say_q.put(("ring", "Time to stretch."))
        self.until(lambda: e._play.called, "no ring")

    def test_a_timer_inside_the_hotkey_window_still_closes_the_mic(self):
        from ai_linux.engine import service
        e = self.make("hotkey")
        with mock.patch.object(service, "LISTEN_NOW_S", 0.3):
            e.listen_now()
        self.ring_during(e)
        self.until(lambda: not e.capturing, "the mic stayed open")
        self.assertEqual(e.state, "idle")

    def test_a_timer_in_a_follow_up_window_still_closes_the_mic_when_off(self):
        e = self.make("off")
        e.followup_until = time.monotonic() + 0.3
        e.set_state("followup")
        self.ring_during(e)
        self.until(lambda: not e.capturing, "the mic stayed open")
        self.assertEqual(e.state, "muted")


class ListenLoopIdleTests(unittest.TestCase):
    def test_mic_off_listen_loop_waits_instead_of_polling(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        e._hold = mock.Mock(wraps=threading.Event())
        t = threading.Thread(target=e._listen_loop, daemon=True)
        t.start()
        time.sleep(0.5)
        e._stop.set()
        self.assertLessEqual(e._hold.is_set.call_count, 2)  # was 20 wake-ups a second while muted

    def test_mic_turned_on_wakes_the_listen_loop(self):
        from ai_linux.engine.service import Engine
        e = Engine(lambda ev: None, {})
        frames = __import__("queue").Queue()
        e.audio = mock.Mock(get_sample_queue=mock.Mock(return_value=frames))
        t = threading.Thread(target=e._listen_loop, daemon=True)
        t.start()
        time.sleep(0.1)
        e._capture(True)
        frames.put((np.zeros(512, np.float32), 0.0))
        deadline = time.monotonic() + 0.5
        while not frames.empty() and time.monotonic() < deadline:
            time.sleep(0.01)
        e._stop.set()
        self.assertTrue(frames.empty())


class LlmStreamTests(unittest.TestCase):
    def test_broken_stream_is_an_llm_error(self):
        from ai_linux.engine import llm

        class Resp:
            def __iter__(self):
                yield b'{"message": {"content": "Hel"}}\n'
                raise TimeoutError("timed out")

            def close(self):
                pass

        # the brain then says it can't reach the model, not "something went wrong"
        with mock.patch.object(llm, "_post", return_value=Resp()), self.assertRaises(llm.LLMError):
            list(llm.chat("http://127.0.0.1:9", "m", []))

    def test_garbled_stream_line_is_an_llm_error(self):
        from ai_linux.engine import llm
        resp = mock.MagicMock()
        resp.__iter__.return_value = iter([b"<html>proxy error</html>\n"])
        with mock.patch.object(llm, "_post", return_value=resp), self.assertRaises(llm.LLMError):
            list(llm.chat("http://127.0.0.1:9", "m", []))
