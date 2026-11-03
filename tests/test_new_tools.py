"""Timers, web access, app and folder tools, quick rules, sounds and the pill's reply pages (no network, no audio)."""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import apps as appdb  # noqa: E402
from ai_linux.engine import sounds, web  # noqa: E402
from ai_linux.engine import tools as T  # noqa: E402
from ai_linux.engine.quick import quick  # noqa: E402
from ai_linux.engine.timers import Timers, spoken_span  # noqa: E402
from ai_linux.ui import caption_model as cm  # noqa: E402

APPS = [appdb.App("org.gnome.Calculator", "Calculator", (), "Perform calculations", "gnome-calculator", ("Utility",)),
        appdb.App("brave-x-Default", "Notion", (), "", "brave-browser", ("Network",), pwa=True),
        appdb.App("org.gnome.TextEditor", "Text Editor", ("editor",), "Edit text files", "gnome-text-editor",
                  ("Utility",)),
        appdb.App("vlc", "VLC", (), "Play music and video", "vlc", ("AudioVideo",))]


class Runs:
    def __init__(self):
        self.calls = []

    def run_argv(self, argv, *, tool_name, args=None, confirm=None, resource_caps=True, timeout=20):
        self.calls.append(argv)
        return {"returncode": 0, "stdout": "", "stderr": ""}


def env(**kw):
    e = T.Env(Runs(), apps=lambda: APPS, on_bus=lambda name: False, running=lambda exe: False)
    for k, v in kw.items():
        setattr(e, k, v)
    return e


class QuickTests(unittest.TestCase):
    def test_sleep_phrases(self):
        for text in ("stop", "Stop.", "that's all", "okay, that's all thanks", "go to sleep", "never mind",
                     "you can go to sleep now", "bye", "good night"):
            self.assertEqual(quick(text).tool, "go_to_sleep", text)

    def test_not_sleep(self):
        for text in ("stop the music", "play music", "open files", "what's the weather", "hi there, open files"):
            q = quick(text)
            self.assertTrue(q is None or q.tool != "go_to_sleep", text)


class TimerTests(unittest.TestCase):
    def test_parse(self):
        e = env()
        self.assertEqual(T._p_timer("set a timer for 5 minutes", e), {"action": "set", "message": "", "seconds": 300})
        self.assertEqual(T._p_timer("remind me in 10 minutes to call mom", e)["message"], "call mom")
        self.assertEqual(T._p_timer("remind me to call mom in 10 minutes", e),
                         {"action": "set", "message": "call mom", "seconds": 600})
        self.assertEqual(T._p_timer("remind me at 5 pm to drink water", e)["at"], "17:00")
        self.assertEqual(T._p_timer("cancel my timers", e)["action"], "cancel")
        self.assertEqual(T._p_timer("remind me in 10 minutes to stop the oven", e)["action"], "set")
        self.assertEqual(T._p_timer("set a timer for twenty five minutes", e)["seconds"], 1500)
        self.assertEqual(T._p_timer("remind me in 5 minutes to take a 10 minute break", e)["seconds"], 300)
        self.assertEqual(T.span_seconds("an hour and a half"), 5400)
        self.assertEqual(T._p_timer("remind me at 5 to drink water", e)["exact"], False)
        self.assertEqual(T._p_timer("how long is left on my timer", e), {"action": "list"})
        self.assertEqual(T.span_seconds("one hour and thirty minutes"), 5400)
        self.assertEqual(T.span_seconds("half an hour"), 1800)
        self.assertEqual(T._p_timer("set a timer", e), {"action": "set", "message": ""})  # asks how long
        self.assertIsNone(T._p_timer("what time is it", e))

    def test_spoken_span(self):
        self.assertEqual(spoken_span(300), "5 minutes")
        self.assertEqual(spoken_span(3660), "1 hour and 1 minute")
        self.assertEqual(spoken_span(45), "45 seconds")

    def test_ring_keep_and_cancel(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "timers.json"
        rang, done = [], threading.Event()
        timers = Timers(path, lambda msg: (rang.append(msg), done.set()))
        timers.start()
        timers.add(1, "tea")
        later = timers.add(600, "call mom")
        self.assertIn("call mom", path.read_text())
        self.assertTrue(done.wait(3))
        self.assertEqual(rang, ["tea"])
        self.assertEqual([it["id"] for it in timers.pending()], [later["id"]])
        timers.close()
        again = Timers(path, rang.append)  # a restart keeps the pending one
        again.start()
        self.assertEqual([it["message"] for it in again.pending()], ["call mom"])
        self.assertEqual(again.cancel(), 1)
        self.assertEqual(again.pending(), [])

    def test_rings_on_time_after_the_laptop_slept(self):
        from ai_linux.engine import timers as timers_mod
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        now = [1_000_000.0]
        done = threading.Event()
        with unittest.mock.patch.object(timers_mod, "CHECK_S", 0.05, create=True):
            timers = Timers(Path(tmp.name) / "t.json", lambda m: done.set(), clock=lambda: now[0])
            self.addCleanup(timers.close)
            timers.add(3600, "stretch")
            now[0] += 3600  # suspended for the hour: the wall clock moved, a monotonic wait did not
            self.assertTrue(done.wait(2))

    def test_every_change_is_announced(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        seen, done = [], threading.Event()
        timers = Timers(Path(tmp.name) / "t.json", lambda m: done.set(),
                        on_change=lambda items: seen.append([it["message"] for it in items]))
        self.addCleanup(timers.close)
        timers.start()
        later = timers.add(600, "call mom")
        timers.add(0.05, "tea")
        self.assertTrue(done.wait(2))
        time.sleep(0.05)
        timers.cancel([later["id"]])
        self.assertEqual(seen, [[], ["call mom"], ["tea", "call mom"], ["call mom"], []])  # soonest first

    def test_missed_while_closed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "timers.json"
        now = time.time()
        path.write_text(f'[{{"id": "a", "due": {now - 60}, "message": "late"}}, '
                        f'{{"id": "b", "due": {now - 7200}, "message": "too old"}}]')
        rang, done = [], threading.Event()
        Timers(path, lambda m: (rang.append(m), done.set())).start()
        self.assertTrue(done.wait(2))
        time.sleep(0.05)
        self.assertEqual(rang, ["late"])

    def test_run(self):
        added = []

        class Fake:
            def add(self, seconds, message):
                added.append((round(seconds), message))

        r = T.run_timer({"action": "set", "seconds": 300, "message": "call mom"}, env(timers=Fake()))
        self.assertEqual(r.say, "Okay, I'll remind you to call mom in 5 minutes.")
        self.assertEqual(T.run_timer({"seconds": 90}, env(timers=Fake())).say, "Timer set for 1 minute and 30 seconds.")
        self.assertEqual(added, [(300, "call mom"), (90, "")])


class WebTests(unittest.TestCase):
    def fetch(self, url):
        self.urls.append(url)
        if "geocoding" in url:
            return {"results": [{"name": "Pune", "latitude": 18.5, "longitude": 73.9}]}
        if "forecast" in url:
            return {"current": {"temperature_2m": 24.4, "apparent_temperature": 28.0, "weather_code": 2},
                    "daily": {"weather_code": [61, 0], "temperature_2m_min": [21, 20], "temperature_2m_max": [30, 31],
                              "precipitation_probability_max": [70, 5]}}
        if "duckduckgo" in url:
            return {"AbstractText": "Ada Lovelace was a mathematician. She wrote the first program. More text."}
        return {}

    def setUp(self):
        self.urls = []

    def test_weather(self):
        self.assertEqual(web.weather("pune", "now", self.fetch), "In Pune it's 24 degrees and partly cloudy, "
                                                                 "feels like 28.")
        self.assertEqual(web.weather("pune", "tomorrow", self.fetch),
                         "Tomorrow in Pune: clear, 20 to 31 degrees, 5 percent chance of rain.")
        self.assertTrue(all(u.startswith("https://") for u in self.urls))

    def test_lookup(self):
        self.assertEqual(web.lookup("ada lovelace", self.fetch),
                         "Ada Lovelace was a mathematician. She wrote the first program.")

    def test_off_by_default(self):
        e = env()
        self.assertEqual(T.run_weather({"city": "Pune"}, e).say, T.WEB_OFF)
        self.assertEqual(T.run_lookup({"query": "x"}, e).say, T.WEB_OFF)
        e.setting = {"web_access": True}.get
        self.assertIn("Which city", T.run_weather({}, e).say)

    def test_parse(self):
        e = env()
        self.assertEqual(T._p_weather("weather in Pune tomorrow", e), {"city": "Pune", "when": "tomorrow"})
        self.assertEqual(T._p_weather("is it going to rain today", e), {"city": "", "when": "today"})
        self.assertEqual(T._p_lookup("who is Ada Lovelace?", e), {"query": "Ada Lovelace"})
        self.assertIsNone(T._p_lookup("Ada Lovelace", e))


class AppAndFolderTests(unittest.TestCase):
    def test_apps(self):
        e = env()
        self.assertEqual(T._p_apps("what does calculator do", e), {"app": "org.gnome.Calculator"})
        self.assertEqual(T._p_apps("what apps do I have", e), {})
        self.assertEqual(T.run_apps({"app": "org.gnome.Calculator"}, e).say, "Calculator: Perform calculations.")
        whole = T.run_apps({}, e)
        self.assertTrue(whole.say.startswith("You have 4 apps: 2 utilities"))
        self.assertIn("VLC: Play music and video", whole.output)

    def test_switch_and_close(self):
        e = env()
        self.assertEqual(T._p_switch("switch to the text editor", e), {"app": "org.gnome.TextEditor"})
        self.assertEqual(T._p_close("close calculator", e), {"app": "org.gnome.Calculator"})
        self.assertEqual(T.run_close({"app": "org.gnome.Calculator"}, e).say, "Calculator isn't open.")
        self.assertIn("browser", T.run_close({"app": "brave-x-Default"}, e).say)
        e.on_bus = lambda name: name == "org.gnome.Calculator"
        self.assertEqual(T.run_close({"app": "org.gnome.Calculator"}, e).say, "Closing Calculator.")
        self.assertIn("quit", e.executor.calls[-1])
        e.running = lambda exe: exe == "vlc"
        T.run_close({"app": "vlc"}, e)
        self.assertEqual(e.executor.calls[-1][:3], ["pkill", "-TERM", "-x"])

    def test_list_folder(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for i, name in enumerate(("old.txt", "new.pdf")):
            p = Path(tmp.name) / name
            p.write_text("x")
            t = time.time() - 100 + i * 50
            import os
            os.utime(p, (t, t))
        orig = T._folder_path
        T._folder_path = lambda key: Path(tmp.name)
        self.addCleanup(setattr, T, "_folder_path", orig)
        r = T.run_list_folder({"folder": "downloads"}, env())
        self.assertEqual(r.say, "Downloads has 2 items. The newest: new, old.")
        self.assertEqual(T._p_list_folder("what's in my downloads", env()), {"folder": "downloads"})
        self.assertIsNone(T._p_list_folder("open downloads", env()))


class SoundTests(unittest.TestCase):
    def test_every_sound_renders_quietly(self):
        for kind in sounds.NOTES:
            wave = sounds.tone(kind)
            self.assertTrue(0.1 < len(wave) / sounds.RATE < 2.0, kind)
            self.assertAlmostEqual(max(abs(x) for x in wave), sounds.GAIN, places=3)
            self.assertIn(kind, sounds.LABELS)


class PageTests(unittest.TestCase):
    def test_long_reply_pages_evenly(self):
        text = ("I can open apps, files and websites, search the web or YouTube, change the volume and brightness, "
                "control music, lock the screen, take screenshots, tell the time and battery level, read your screen, "
                "write a reply for you and remember things you tell me.")
        cards = cm.pages(text, lambda s: len(s) <= 120)
        self.assertEqual(" ".join(cards), " ".join(text.split()))
        self.assertTrue(all(len(c) <= 120 for c in cards))
        self.assertGreater(min(len(c) for c in cards), 40)  # no lone last words
        self.assertEqual(cm.pages("Short.", lambda s: True), ["Short."])
        starts = cm.page_starts(cards, 12.0)
        self.assertEqual(starts[0], 0.0)
        self.assertTrue(starts == sorted(starts) and starts[-1] < 12.0)


class ReviewFixTests(unittest.TestCase):
    def test_close_never_guesses_the_process(self):
        e = env(running=lambda exe: True)
        launcher = appdb.App("org.x.Tool", "Tool", (), "", "", ("Utility",))  # Exec started with python3
        flat = appdb.App("org.y.Flat", "Flat", (), "", "flatpak", ("Utility",))
        e.apps = lambda: APPS + [launcher, flat]
        self.assertIn("close its window", T.run_close({"app": "org.x.Tool"}, e).say)
        self.assertIn("close its window", T.run_close({"app": "org.y.Flat"}, e).say)
        self.assertEqual(e.executor.calls, [])
        self.assertEqual(appdb.exe_name(["env", "A=1", "/snap/bin/code", "%F"]), "code")
        self.assertEqual(appdb.exe_name(["python3", "/opt/tool.py"]), "")

    def test_switch_opens_a_closed_app_and_never_a_second_window(self):
        e = env()
        self.assertIn("wasn't open", T.run_switch({"app": "org.gnome.Calculator"}, e).say)
        self.assertIn("gtk-launch", e.executor.calls[-1])
        e.running = lambda exe: True
        n = len(e.executor.calls)
        self.assertIn("can't bring", T.run_switch({"app": "org.gnome.Calculator"}, e).say)
        self.assertEqual(len(e.executor.calls), n)
        self.assertEqual(T._object_path("org.gnome.Text-Editor"), "/org/gnome/Text_Editor")

    def test_list_folder_survives_a_broken_link(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "file.txt").write_text("x")
        (Path(tmp.name) / "gone").symlink_to(Path(tmp.name) / "missing")
        orig = T._folder_path
        T._folder_path = lambda key: Path(tmp.name)
        self.addCleanup(setattr, T, "_folder_path", orig)
        self.assertTrue(T.run_list_folder({"folder": "downloads"}, env()).ok)

    def test_weather_time_words_are_not_a_city(self):
        self.assertEqual(T._p_weather("weather in the evening", env())["city"], "")
        self.assertEqual(T._p_weather("weather for tomorrow", env()), {"city": "", "when": "tomorrow"})

    def test_memory_questions_are_not_saved(self):
        self.assertIsNone(T.parse_memory("Can you remember what I said?"))
        self.assertIsNone(T.parse_memory("remember if the oven is on"))
        self.assertEqual(T.parse_memory("can you remember my sister is Priya")[0], "remember")

    def test_cancel_one_timer(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        timers = Timers(Path(tmp.name) / "t.json", lambda m: None)
        timers.start()
        self.addCleanup(timers.close)
        timers.add(300, "")
        timers.add(600, "call mom")
        e = env(timers=timers)
        self.assertIn("Say which one", T.run_timer({"action": "cancel", "which": "cancel the timer"}, e).say)
        T.run_timer(T._p_timer("cancel the 5 minute timer", e), e)
        self.assertEqual([it["message"] for it in timers.pending()], ["call mom"])
        T.run_timer(T._p_timer("delete the reminder about mom", e), e)
        self.assertEqual(timers.pending(), [])

    def test_bare_hour_means_the_next_one(self):
        import datetime
        added = []

        class Fake:
            def add(self, seconds, message):
                added.append(seconds)
        noon = datetime.datetime(2026, 10, 29, 12, 0)
        with unittest.mock.patch.object(T.datetime, "datetime", wraps=datetime.datetime) as dt:
            dt.now.return_value = noon
            T.run_timer({"action": "set", "at": "05:00", "exact": False, "message": "tea"}, env(timers=Fake()))
        self.assertEqual(round(added[0] / 3600), 5)  # 5 pm today, not 5 am tomorrow

    def test_words_before_the_name_only_when_they_are_a_request(self):
        from ai_linux.wake import strip_wake
        self.assertEqual(strip_wake("Stop Phoenix", "phoenix"), "Stop")
        self.assertEqual(strip_wake("I told Phoenix", "phoenix"), "")


class RequestFixTests(unittest.TestCase):
    """Wrong answers seen on the laptop: changing the voice, YouTube, music with no player, timers with no time."""

    def test_change_your_voice_picks_the_next_one(self):
        names = list(T.VOICES)
        for now, want in ((names[0], names[1]), ("supertonic:F1", "supertonic:F2"), (names[-1], names[0])):
            self.assertEqual(T._p_voice("change your voice", env(setting={"voice": now}.get)), {"voice": want}, now)
        e = env(setting={"voice": "supertonic:F3"}.get)
        for text in ("Change your voice.", "switch your voice please", "can you change your voice",
                     "use a different voice", "next voice"):
            self.assertEqual(T._p_voice(text, e), {"voice": "supertonic:F4"}, text)

    def test_default_voice_is_supertonic_f1(self):
        e = env(setting={"voice": "supertonic:M3"}.get)
        for text in ("change your voice to the default", "default voice", "use your default voice please"):
            self.assertEqual(T._p_voice(text, e), {"voice": "supertonic:F1"}, text)
        self.assertEqual(T._p_voice("use male voice 4", e), {"voice": "supertonic:M4"})
        self.assertEqual(T._p_voice("switch to a male voice", e), {"voice": "supertonic:M1"})

    def test_youtube_requests_that_start_with_can_you(self):
        for text in ("can you play Believer on YouTube", "Could you play Believer on YouTube?",
                     "can you please play Believer on YouTube", "play Believer on YouTube"):
            q = quick(text)
            self.assertEqual((q.tool, q.args), ("search_web", {"query": "Believer", "site": "youtube", "play": True}),
                             text)
        q = quick("could you find lofi beats on YouTube")
        self.assertEqual(q.args, {"query": "lofi beats", "site": "youtube"})  # a search shows the results

    def test_play_on_youtube_opens_the_first_video_with_web_access(self):
        e = env(setting={"web_access": True}.get)
        with unittest.mock.patch.object(web, "first_video", return_value="abcdefghijk") as first:
            r = T.run_search({"query": "Believer", "site": "youtube", "play": True}, e)
        first.assert_called_once_with("Believer")
        self.assertEqual(e.executor.calls[-1],
                         ["setsid", "-f", "xdg-open", "https://www.youtube.com/watch?v=abcdefghijk"])
        self.assertEqual((r.ok, r.say), (True, "Playing Believer on YouTube."))

    def test_play_on_youtube_falls_back_to_the_results(self):
        results = "https://www.youtube.com/results?search_query=Believer"
        play = {"query": "Believer", "site": "youtube", "play": True}
        with unittest.mock.patch.object(web, "first_video", side_effect=AssertionError("web access is off")):
            e = env()
            T.run_search(play, e)
            self.assertEqual(e.executor.calls[-1][-1], results)
            e = env(setting={"web_access": True}.get)
            T.run_search({"query": "Believer", "site": "youtube"}, e)  # a search, not a play
            self.assertEqual(e.executor.calls[-1][-1], results)
        for found in ({"return_value": None}, {"side_effect": OSError("offline")}):
            e = env(setting={"web_access": True}.get)
            with unittest.mock.patch.object(web, "first_video", **found):
                self.assertTrue(T.run_search(play, e).ok)
            self.assertEqual(e.executor.calls[-1][-1], results)

    def test_first_video_reads_the_results_page(self):
        page = ('{"promotedVideoRenderer":{"videoId":"AAAAAAAAAAA"}},{"videoRenderer":{"videoId":"7wtfhZwyrcc",'
                '"thumbnail":{}}},{"videoRenderer":{"videoId":"BBBBBBBBBBB"}}')
        urls = []
        self.assertEqual(web.first_video("Believer song", fetch=lambda u: (urls.append(u), page)[1]), "7wtfhZwyrcc")
        self.assertEqual(urls, ["https://www.youtube.com/results?search_query=Believer+song"])
        self.assertIsNone(web.first_video("x", fetch=lambda u: "<html>Before you continue</html>"))

    def test_play_music_with_no_player_searches_youtube(self):
        e = env()
        for text in ("play some music", "play a song", "Play music please", "can you play some music"):
            self.assertEqual(T._p_media(text, e), {"action": "play", "music": True}, text)
        self.assertEqual(T._p_media("play", e), {"action": "play"})
        self.assertEqual(T._p_media("resume the music", e), {"action": "play"})

        class NoPlayer(Runs):
            def run_argv(self, argv, **kw):
                super().run_argv(argv, **kw)
                if argv[0] == "playerctl":
                    return {"returncode": 1, "stdout": "", "stderr": "No players found"}
                return {"returncode": 0, "stdout": "", "stderr": ""}

        e = env(executor=NoPlayer())
        r = T.run_media({"action": "play", "music": True}, e)
        self.assertTrue(r.ok)
        youtube = ["setsid", "-f", "xdg-open", "https://www.youtube.com/results?search_query=music"]
        self.assertEqual(e.executor.calls, [["playerctl", "play"], youtube])
        r = T.run_media({"action": "play"}, env(executor=NoPlayer()))  # "resume" with nothing to resume
        self.assertEqual((r.ok, r.say), (False, "Nothing is playing."))
        e = env()
        self.assertTrue(T.run_media({"action": "play", "music": True}, e).ok)  # a player is there: it plays
        self.assertEqual(e.executor.calls, [["playerctl", "play"]])

    def test_timer_with_no_time_asks_how_long(self):
        e = env()
        self.assertEqual(T._p_timer("remind me to stretch", e), {"action": "set", "message": "stretch"})
        self.assertEqual(T._p_timer("can you set a reminder", e), {"action": "set", "message": ""})
        added = []

        class Fake:
            def add(self, seconds, message):
                added.append((round(seconds), message))

        r = T.run_timer({"action": "set", "message": "stretch"}, env(timers=Fake()))
        self.assertEqual((r.ok, r.say), (True, "When should I remind you to stretch?"))
        self.assertEqual(r.followup("in ten minutes"), {"action": "set", "message": "stretch", "seconds": 600})
        self.assertEqual(r.followup("5 minutes."), {"action": "set", "message": "stretch", "seconds": 300})
        self.assertEqual(r.followup("at 5 pm")["at"], "17:00")
        self.assertIsNone(r.followup("what's the weather"))
        self.assertIsNone(r.followup("remind me in 5 minutes to call mom"))  # a new request, handled as one
        r = T.run_timer({"action": "set", "message": ""}, env(timers=Fake()))
        self.assertEqual(r.say, "How long should the timer be?")
        self.assertEqual(r.followup("half an hour"), {"action": "set", "message": "", "seconds": 1800})
        self.assertEqual(added, [])
        self.assertIsNone(T.run_timer({"seconds": 60}, env(timers=Fake())).followup)


if __name__ == "__main__":
    unittest.main()
