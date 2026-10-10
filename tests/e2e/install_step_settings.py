"""Writes config/settings.yaml the way `/butler-setup install` does (course Module 2): the
butler's name, the people, the town and the weather, and everything later steps fill in left
empty. Run from the install folder with its own Python. All names are invented."""

from pathlib import Path

import yaml

s = yaml.safe_load(Path("config/settings.example.yaml").read_text())
s["name"] = "Bento"
s["email"].update(sender="", sender_name="Bento", recipients=[], trusted_senders=[])
s["household"]["adults"] = [{"name": "Sam", "emails": []}, {"name": "Alex", "emails": []}]
s["household"]["about"] = ""
s["people"] = [{"name": n, "aliases": [n], "calendars": [], "color": c}
               for n, c in [("Sam", "#3f51b5"), ("Alex", "#e67c73"), ("Mia", "#33b679"), ("Pat", "#8e24aa")]]
s["calendar"].update(include=[], write_to="")
s["reminders"]["todo_list"] = "Bento"
s["portal"]["url"] = ""
for key in ("language_learning", "score_tracker"):
    s.pop(key, None)
Path("config/settings.yaml").write_text(yaml.safe_dump(s, sort_keys=False, allow_unicode=True))
print("wrote config/settings.yaml")
