"""Todoist: the spoken parser, the API client and the tool, against a local fake of API v1 (no network)."""

from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_linux.engine import todoist  # noqa: E402
from ai_linux.engine import tools as T  # noqa: E402

TOKEN = "dummy-token"
TASKS = [{"id": "1", "content": "Buy milk", "due": {"string": "today"}},
         {"id": "2", "content": "Call mom", "due": {"string": "today"}},
         {"id": "3", "content": "Renew passport", "due": {"string": "Oct 28"}}]


class FakeTodoist:
    """Just enough of https://api.todoist.com/api/v1 for the tool, with dummy tasks."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.tasks = [dict(t) for t in TASKS]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _send(self, code, body=None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    self._send(401, {"error": "Unauthorized"})
                    return False
                return True

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(url.query))
                fake.calls.append(("GET", url.path, q))
                if not self._auth():
                    return
                if url.path != "/api/v1/tasks/filter":
                    return self._send(404, {"error": "not found"})
                query = q.get("query", "")
                if query.startswith("search: "):
                    words = query[8:].lower().split()
                    hits = [t for t in fake.tasks if all(w in t["content"].lower() for w in words)]
                elif query == "overdue":
                    hits = [t for t in fake.tasks if t["due"]["string"] != "today"]
                elif query == "tomorrow":
                    hits = []
                else:
                    hits = list(fake.tasks)
                self._send(200, {"results": hits, "next_cursor": None})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.calls.append(("POST", self.path, body))
                if not self._auth():
                    return
                if self.path == "/api/v1/tasks/quick":
                    text = body["text"]
                    due = "tomorrow at 17:00" if "tomorrow at 5" in text else None
                    content = text.replace(" tomorrow at 5", "")
                    task = {"id": "9", "content": content, "due": {"string": due} if due else None}
                    fake.tasks.append(task)
                    return self._send(200, task)
                if self.path.startswith("/api/v1/tasks/") and self.path.endswith("/close"):
                    tid = self.path.split("/")[-2]
                    fake.tasks = [t for t in fake.tasks if t["id"] != tid]
                    return self._send(204)
                self._send(404, {"error": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class ParseTests(unittest.TestCase):
    def p(self, text):
        return T._p_todoist(text, T.Env(None))

    def test_add(self):
        self.assertEqual(self.p("Add buy milk tomorrow at 5 to Todoist."),
                         {"action": "add", "text": "buy milk tomorrow at 5"})
        self.assertEqual(self.p("add call mom to my to-do list for friday"),
                         {"action": "add", "text": "call mom for friday"})
        self.assertEqual(self.p("put renew passport on my todoist"), {"action": "add", "text": "renew passport"})
        self.assertEqual(self.p("add a task to call the bank"), {"action": "add", "text": "call the bank"})
        self.assertEqual(self.p("create a new Todoist task pay rent on the 1st"),
                         {"action": "add", "text": "pay rent on the 1st"})

    def test_list(self):
        self.assertEqual(self.p("what's on my Todoist today?"), {"action": "list", "when": "today"})
        self.assertEqual(self.p("what tasks do I have tomorrow"), {"action": "list", "when": "tomorrow"})
        self.assertEqual(self.p("any overdue tasks?"), {"action": "list", "when": "overdue"})
        self.assertEqual(self.p("read my to-do list"), {"action": "list", "when": "today"})
        self.assertEqual(self.p("show my tasks for this week"), {"action": "list", "when": "week"})

    def test_complete(self):
        self.assertEqual(self.p("mark buy milk as done"), {"action": "complete", "task": "buy milk"})
        self.assertEqual(self.p("mark buy milk done in Todoist"), {"action": "complete", "task": "buy milk"})
        self.assertEqual(self.p("tick off call mom"), {"action": "complete", "task": "call mom"})
        self.assertEqual(self.p("complete the task pay rent"), {"action": "complete", "task": "pay rent"})

    def test_not_todoist(self):
        for text in ("remind me to buy milk at 5", "add a timer for 5 minutes", "what's the weather today",
                     "mark this email as read", "complete the sentence for me", "open my files", ""):
            self.assertIsNone(self.p(text), text)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeTodoist()
        self.addCleanup(self.fake.close)
        patcher = mock.patch.object(todoist, "API", self.fake.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.asked = []
        self.answer = True
        self.env = T.Env(None, confirm=lambda req: (self.asked.append(req), self.answer)[1],
                         secret={"todoist": TOKEN}.get)

    def test_add_says_the_parsed_due_date(self):
        r = T.run_todoist({"action": "add", "text": "buy milk tomorrow at 5"}, self.env)
        self.assertTrue(r.ok)
        self.assertEqual(r.say, "Added buy milk, due tomorrow at 17:00.")
        self.assertEqual(self.fake.calls[-1][1:], ("/api/v1/tasks/quick", {"text": "buy milk tomorrow at 5"}))

    def test_list_reads_a_short_list(self):
        r = T.run_todoist({"action": "list", "when": "today"}, self.env)
        self.assertEqual(r.say, "You have 3 tasks for today: Buy milk, Call mom and Renew passport.")
        self.assertEqual(self.fake.calls[-1][2]["query"], "today | overdue")
        self.assertEqual(T.run_todoist({"action": "list", "when": "tomorrow"}, self.env).say,
                         "Nothing in Todoist for tomorrow.")
        r = T.run_todoist({"action": "list", "when": "overdue"}, self.env)
        self.assertEqual(r.say, "You have 1 overdue task: Renew passport.")

    def test_long_list_is_cut_for_speaking(self):
        self.fake.tasks = [{"id": str(i), "content": f"Task {i}", "due": None} for i in range(9)]
        r = T.run_todoist({"action": "list"}, self.env)
        self.assertEqual(r.say, "You have 9 tasks for today: Task 0, Task 1, Task 2, Task 3, Task 4 and 4 more.")

    def test_complete_asks_then_closes_the_matching_task(self):
        r = T.run_todoist({"action": "complete", "task": "the milk"}, self.env)
        self.assertTrue(r.ok)
        self.assertEqual(r.say, "Marked Buy milk as done.")
        self.assertEqual(self.asked[0].detail, "Mark Buy milk as done?")
        self.assertEqual(self.fake.calls[-1][:2], ("POST", "/api/v1/tasks/1/close"))

    def test_complete_declined_or_unknown_closes_nothing(self):
        self.answer = False
        self.assertFalse(T.run_todoist({"action": "complete", "task": "call mom"}, self.env).ok)
        r = T.run_todoist({"action": "complete", "task": "water the plants"}, self.env)
        self.assertEqual(r.say, "I couldn't find a Todoist task like water the plants.")
        self.assertFalse(any(c[1].endswith("/close") for c in self.fake.calls))

    def test_without_or_with_a_wrong_token(self):
        r = T.run_todoist({"action": "list"}, T.Env(None))
        self.assertIn("isn't connected", r.say)
        r = T.run_todoist({"action": "list"}, T.Env(None, secret={"todoist": "wrong"}.get))
        self.assertEqual(r.say, "Todoist didn't accept your token. You can change it in Tools, under Todoist.")

    def test_unreachable(self):
        with mock.patch.object(todoist, "API", "http://127.0.0.1:9/api/v1"):
            r = T.run_todoist({"action": "list"}, self.env)
        self.assertEqual(r.say, "I couldn't reach Todoist.")

    def test_model_filled_arguments_in_odd_shapes(self):
        for args in ({}, {"action": "add"}, {"action": "add", "text": ""}, {"action": "nope"},
                     {"action": "complete"}, {"action": "list", "when": "someday"}, {"action": ["add"]}):
            T.run_todoist(args, self.env)  # never raises; asks or reads the default list


class MenuTests(unittest.TestCase):
    def test_todoist_on_the_menu_only_with_a_token(self):
        from ai_linux.engine.brain import Brain
        settings = {"web_access": False}.get
        without = Brain(settings, T.Env(None), speak=lambda s: None)
        with_token = Brain(settings, T.Env(None, secret={"todoist": TOKEN}.get), speak=lambda s: None)
        self.assertNotIn("todoist", [t.name for t in without.tools()])
        self.assertIn("todoist", [t.name for t in with_token.tools()])


class UnsureRouterTests(unittest.TestCase):
    def test_finishing_a_task_asks_once_when_the_router_was_unsure(self):
        import threading
        from ai_linux.engine import brain as B
        fake = FakeTodoist()
        self.addCleanup(fake.close)
        asked = []
        env = T.Env(None, confirm=lambda r: (asked.append(r.detail), True)[1], secret={"todoist": TOKEN}.get)
        env.executor = mock.Mock(gate=mock.Mock(check=mock.Mock(return_value=mock.Mock(allowed=True))))
        settings = {"escalate_threshold": 0.85, "action_feedback": "speech", "wake_word": "phoenix"}.get
        b = B.Brain(settings, env, speak=lambda s: None)
        with mock.patch.object(todoist, "API", fake.base), \
                mock.patch.object(b, "route", return_value=("todoist", 0.6, "jiwo", {"todoist": 0.6})):
            turn = b.handle("mark call mom as done", cancel=threading.Event())
        self.assertEqual(asked, ["Mark Call mom as done?"])
        self.assertEqual((turn["reply"], turn["route"]), ("Marked Call mom as done.", "small"))

    def run_with_actions(self, armed, args):
        import threading
        from types import SimpleNamespace
        from ai_linux.engine import brain as B
        from ai_linux.safety import ActionGate
        fake = FakeTodoist()
        self.addCleanup(fake.close)
        env = T.Env(SimpleNamespace(gate=ActionGate(armed=armed, confirm_globs=[])), confirm=lambda r: True,
                    secret={"todoist": TOKEN}.get)
        b = B.Brain({"escalate_threshold": 0.85, "wake_word": "phoenix"}.get, env, speak=lambda s: None)
        turn = {"tools": [], "heard": "x", "latency": {}}
        with mock.patch.object(todoist, "API", fake.base):
            res = b._run_tools([("todoist", args)], turn, threading.Event(), speak=False)[0]
        return res, turn["tools"][0]["verdict"], [c for c in fake.calls if c[0] == "POST"]

    def test_actions_off_stop_changes_but_not_reading(self):
        for args in ({"action": "add", "text": "buy milk"}, {"action": "complete", "task": "call mom"}):
            res, verdict, posted = self.run_with_actions(False, args)
            self.assertEqual((res.ok, verdict, posted), (False, "denied", []), args)
        res, verdict, _ = self.run_with_actions(False, {"action": "list"})
        self.assertEqual((res.ok, verdict), (True, "ran"))

    def test_actions_on_allow_changes(self):
        res, verdict, posted = self.run_with_actions(True, {"action": "add", "text": "buy milk"})
        self.assertEqual((res.ok, verdict, len(posted)), (True, "ran", 1))

if __name__ == "__main__":
    unittest.main()
