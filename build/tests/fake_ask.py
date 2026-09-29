"""A stand-in for console/ask.py that passes every call to the real one and
then changes its reply, so digest.py can be tested against consoles that do
not exist yet or no longer do. Chosen by FAKE_ASK_MODE:

    old      no `digest` verb (refused as bad_request)
    legacy   a console from before team review was removed: `answers` lists two
             views of the team, `list` counts them (digest.py must ignore both)
    garbage  prints something that is not JSON

FAKE_ASK_LOG, when set, gets one line per call: the arguments, as JSON.
"""

import json
import os
import subprocess
import sys

REAL = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "console", "ask.py")
mode = os.environ.get("FAKE_ASK_MODE", "")
args = sys.argv[1:]
if os.environ.get("FAKE_ASK_LOG"):
    with open(os.environ["FAKE_ASK_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(args) + "\n")
verb = next((a for a in args if a in ("list", "answers", "digest", "add", "applied", "withdraw")), None)
if mode == "garbage":
    print("Traceback: not JSON")
    sys.exit(1)
if mode == "old" and verb == "digest":
    print(json.dumps({"ok": False, "code": "bad_request", "message": "invalid choice: 'digest'", "params": {}}))
    sys.exit(2)
p = subprocess.run([sys.executable, REAL, *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
doc = json.loads(p.stdout)


if mode == "legacy":
    if verb == "answers":
        first = doc["answers"][0]["id"] if doc.get("answers") else "none"
        doc["advice"] = [
            {"seq": 90, "at": "2026-03-03T10:00:00Z", "by": "web:ana", "id": first, "title": "",
             "on": "answer", "on_seq": 1, "value": "yes", "stance": "disagree",
             "reason": "too slow for the rainy season", "current": True, "verified": True},
            {"seq": 91, "at": "2026-03-03T10:05:00Z", "by": "web:li", "id": first, "title": "",
             "on": "ask", "on_seq": 1, "value": "yes", "stance": "agree", "reason": "",
             "current": True, "verified": True}]
    if verb == "list":
        for r in doc.get("asks", []):
            r["advice"] = {"agree": 2, "disagree": 1}
print(json.dumps(doc, ensure_ascii=False))
sys.exit(p.returncode)
