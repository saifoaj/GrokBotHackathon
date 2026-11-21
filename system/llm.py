"""Shared Grok client: ask the same question 5 times, use the answer only when all 5 agree.

Every agreed answer (and every split) is saved under the exact question, so a later run
reads the saved answer and makes no call: the same input always gives the same output.
Settings come from the environment or the repository's .env file:

    GROK_API_KEY   (XAI_API_KEY is also accepted)
    GROK_MODEL     model name, default grok-4.7
    LLM=0          never call the model (saved answers are still used)
"""

import hashlib
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.7"
VOTES = 5
CACHE_DIR = os.environ.get("LLM_CACHE") or os.path.join(ROOT, ".cache", "llm")


def _dotenv():
    out = {}
    path = os.path.join(ROOT, ".env")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def setting(name, default=""):
    value = os.environ.get(name, "").strip()
    return value or _dotenv().get(name, "") or default


def api_key():
    return setting("GROK_API_KEY") or setting("XAI_API_KEY")


def model():
    return setting("GROK_MODEL", DEFAULT_MODEL)


def enabled():
    return setting("LLM").lower() not in ("0", "false", "no", "off")


def parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def chat(system, payload, max_tokens=2000):
    """One call at temperature 0 with a JSON reply. Retries network errors and rate limits."""
    body = {"model": model(), "temperature": 0, "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}
    data = json.dumps(body).encode("utf-8")
    last = None
    for attempt in range(5):
        request = urllib.request.Request(API_URL, data=data, headers={
            "Authorization": "Bearer " + api_key(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                reply = json.loads(response.read().decode("utf-8"))
            return parse_json(reply["choices"][0]["message"]["content"])
        except urllib.error.HTTPError as error:
            last = "HTTP %s %s" % (error.code, error.read().decode("utf-8", "replace")[:200])
            if error.code not in (408, 409, 429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, ValueError) as error:
            last = repr(error)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


class Store:
    """Saved answers for one kind of question: {key: {"answer": ..., "agreed": bool, "votes": [...]}}."""

    def __init__(self, name):
        self.path = os.path.join(CACHE_DIR, name + ".jsonl")
        self.rows = {}
        self.lock = threading.Lock()
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        self.rows[row["key"]] = row

    def key(self, prompt, item):
        raw = json.dumps({"model": model(), "votes": VOTES, "prompt": prompt, "item": item},
                         sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def get(self, key):
        return self.rows.get(key)

    def put(self, rows):
        if not rows:
            return
        os.makedirs(CACHE_DIR, exist_ok=True)
        with self.lock, open(self.path, "a", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                self.rows[row["key"]] = row
                handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def vote_batch(store, prompt, items, read, batch=25, label="items"):
    """Answer every item by 5 unanimous votes. Returns {item_key: answer or None}.

    items: list of (item_key, item) where item is JSON-able and item_key is the string the reply
    uses to name it. read(reply, item_key) -> one normalised answer (None if missing or unusable).
    Items already saved are not asked again; a split vote is saved as None and stays None.
    """
    out, todo = {}, []
    for item_key, item in items:
        row = store.get(store.key(prompt, item))
        if row is not None:
            out[item_key] = row["answer"] if row["agreed"] else None
        else:
            todo.append((item_key, item))
    if not todo:
        return out
    if not (enabled() and api_key()):
        sys.stderr.write("llm: %d %s not normalised (no GROK_API_KEY or LLM=0)\n" % (len(todo), label))
        for item_key, _ in todo:
            out[item_key] = None
        return out
    chunks = [todo[i:i + batch] for i in range(0, len(todo), batch)]
    jobs = [(c, v) for c in range(len(chunks)) for v in range(VOTES)]
    replies = {}

    def run(job):
        c, _v = job
        try:
            return job, chat(prompt, {"items": [item for _, item in chunks[c]]})
        except RuntimeError as error:
            sys.stderr.write("llm: call failed (%s)\n" % error)
            return job, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        for job, reply in pool.map(run, jobs):
            replies[job] = reply
    rows, agreed = [], 0
    for c, chunk in enumerate(chunks):
        for item_key, item in chunk:
            votes = [read(replies[(c, v)], item_key) if replies[(c, v)] is not None else "__failed__"
                     for v in range(VOTES)]
            if "__failed__" in votes:
                out[item_key] = None  # not saved: asked again next run
                continue
            same = all(json.dumps(x, sort_keys=True) == json.dumps(votes[0], sort_keys=True) for x in votes)
            answer = votes[0] if same else None
            agreed += same
            out[item_key] = answer
            rows.append({"key": store.key(prompt, item), "item": item, "answer": answer, "agreed": same,
                         "votes": votes})
    store.put(rows)
    sys.stderr.write("llm: %s: %d asked, %d agreed 5/5, %d split (rules kept)\n" %
                     (label, len(todo), agreed, len(rows) - agreed))
    return out
