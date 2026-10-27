"""Builds data/eval.jsonl: hand-written + MASSIVE (CC BY 4.0) + CLINC150 (CC BY 3.0). Labels assume the 26-tool menu."""

import gzip
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from synthetic import S  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
rng = random.Random(7)

MASSIVE_MAP = {"audio_volume_down": "set_volume", "audio_volume_up": "set_volume", "audio_volume_mute": "set_volume",
               "alarm_set": "set_timer", "datetime_query": "get_time", "weather_query": "get_weather"}
MASSIVE_NONE = ["general_joke", "general_quirky", "qa_factoid", "qa_definition", "qa_maths", "cooking_recipe",
                "email_sendemail", "email_query", "social_post", "takeaway_order", "iot_coffee", "iot_cleaning",
                "calendar_query", "play_game", "music_likeness"]
CLINC_MAP = {"timer": "set_timer", "alarm": "set_timer", "reminder_update": "set_timer", "weather": "get_weather",
             "time": "get_time", "date": "get_time", "change_volume": "set_volume", "change_accent": "set_voice"}
# ambiguous for a desktop assistant (could be web search, memory, sleep, music search): left out
CLINC_SKIP = {"goodbye", "todo_list", "todo_list_update", "shopping_list", "shopping_list_update", "calendar",
              "calendar_update", "play_music", "sync_device", "whisper_mode", "change_speed", "change_ai_name",
              "change_user_name", "reminder", "next_song", "find_phone", "travel_suggestion", "restaurant_suggestion",
              "flight_status", "traffic", "directions", "distance", "exchange_rate", "current_location", "news"}

rows = []
for label, texts in S.items():
    rows += [{"text": t, "label": label, "source": "handwritten"} for t in texts]

massive = [json.loads(line) for line in gzip.open(BASE / "data/massive_en_test.json.gz", "rt")]
by = defaultdict(list)
for r in massive:
    by[r["label_text"]].append(r["text"])
for intent, tool in MASSIVE_MAP.items():
    picks = by[intent] if tool == "set_volume" else rng.sample(by[intent], min(25, len(by[intent])))
    rows += [{"text": t, "label": tool, "source": f"massive:{intent}"} for t in picks]
for intent in MASSIVE_NONE:
    rows += [{"text": t, "label": "none", "source": f"massive:{intent}"} for t in rng.sample(by[intent], min(9, len(by[intent])))]

clinc = json.load(open(BASE / "data/clinc_data_full.json"))
cby = defaultdict(list)
for t, n in clinc["test"]:
    cby[n].append(t)
cby["oos"] = [t for t, _ in clinc["oos_test"]]
for intent, tool in CLINC_MAP.items():
    rows += [{"text": t, "label": tool, "source": f"clinc:{intent}"} for t in rng.sample(cby[intent], 12)]
rows += [{"text": t, "label": "none", "source": "clinc:oos"} for t in rng.sample(cby["oos"], 120)]
others = [n for n in cby if n not in CLINC_MAP and n not in CLINC_SKIP and n != "oos"]
for n in rng.sample(others, 80):
    rows += [{"text": t, "label": "none", "source": f"clinc:{n}"} for t in rng.sample(cby[n], 1)]

# 30 % dev split (thresholds are fitted there), 70 % test, stratified by label
rng.shuffle(rows)
seen = defaultdict(int)
for r in rows:
    seen[r["label"]] += 1
    r["split"] = "dev" if seen[r["label"]] % 10 in (1, 4, 7) else "test"
(BASE / "data/eval.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
from collections import Counter  # noqa: E402
print(len(rows), Counter(r["split"] for r in rows), Counter(r["label"] for r in rows).most_common())
