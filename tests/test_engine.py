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

    def test_cold_big_model_says_one_moment(self):
        b, said, cues = self.make({"none": 0.99})
        with mock.patch.object(brain_mod.llm, "loaded", return_value=False), \
                mock.patch.object(brain_mod.llm, "chat", return_value=iter([("text", "Sure."), ("done", {})])):
            b.handle("tell me a joke", "text", threading.Event())
        self.assertEqual(said, ["One moment.", "Sure."])
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
        self.assertEqual(status, ["Waking the big model", "The big model is answering"])
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

    def test_one_moment_goes_back_to_thinking(self):
        e, events = self.make()
        e._play = mock.Mock()
        e._busy, e._think_detail = True, "Waking the big model"
        e._say_wait("One moment.")
        threading.Thread(target=e._speak_loop, daemon=True).start()
        deadline = time.monotonic() + 2
        while e.state != "thinking" and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(e.state, "thinking")
        self.assertIn({"ev": "state", "state": "thinking", "detail": "Waking the big model"}, events)
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



class EchoCancelSetupTests(unittest.TestCase):
    """PipeWire is faked: no pw-cli or pw-record runs."""

    def make(self, exists, alive=True):
        import contextlib
        from ai_linux.engine import audio
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(audio, "VAD"))
        stack.enter_context(mock.patch.object(audio.atexit, "register"))
        proc = mock.Mock(poll=mock.Mock(return_value=None if alive else 0))
        self.popen = stack.enter_context(mock.patch.object(audio.subprocess, "Popen", return_value=proc))
        self.exists = stack.enter_context(mock.patch.object(audio.PipeWireAudioIO, "_node_exists",
                                                            side_effect=exists))
        self.sleep = stack.enter_context(mock.patch.object(audio.time, "sleep"))
        io = audio.PipeWireAudioIO()
        self.popen.reset_mock()
        self.exists.reset_mock()
        self.sleep.reset_mock()
        return io, proc

    def test_failed_echo_cancel_is_not_loaded_again_on_every_mic_open(self):
        io, _ = self.make(exists=lambda name: False)
        io._ensure_aec()  # what each start_listening does
        self.popen.assert_not_called()  # was another pw-cli left running per mic open
        self.assertEqual(self.sleep.call_count, 0)  # and 4 s of waiting in the caller's thread

    def test_echo_cancel_that_came_up_late_is_used(self):
        io, _ = self.make(exists=lambda name: False)
        self.exists.side_effect = lambda name: True  # appeared after the 4 s wait
        io._ensure_aec()
        self.assertTrue(io._aec_ok)
        self.popen.assert_not_called()

    def test_working_echo_cancel_is_not_listed_again(self):
        answers = iter([False, True])
        io, _ = self.make(exists=lambda name: next(answers, True))
        self.assertTrue(io._aec_ok)
        io._ensure_aec()
        self.popen.assert_not_called()
        self.exists.assert_not_called()  # one pw-cli ls per mic open before

    def test_echo_cancel_loads_again_after_pipewire_restarted(self):
        answers = iter([False, True])
        io, proc = self.make(exists=lambda name: next(answers, True))
        proc.poll.return_value = 0  # our pw-cli ended with the old PipeWire, and the module with it
        self.exists.side_effect = iter([False, True])
        io._ensure_aec()
        self.popen.assert_called_once()
        self.assertTrue(io._aec_ok)


class McpCleanupTests(unittest.TestCase):
    SERVER = Path(__file__).with_name("fake_mcp_server.py")

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

            def tools(self):
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
