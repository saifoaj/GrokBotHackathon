"""Check that a free-text reading (note or search snippet) really states the claimed fact.

The rules still extract the date or place. This module only asks whether that reading
is what the text says. A rejected field is dropped; nothing is invented to replace it.
Structured fields (listed dobs, place_of_birth, provider checks) are not sent here.
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
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.20-0309-non-reasoning"
CACHE = os.path.join(ROOT, "debug", "fact_check.jsonl")
COLLECT = "collect"
ENFORCE = "enforce"

PROMPT = """You check one reading of one text. The program has already extracted the facts in "claim". Say whether each fact is what the text states about the subject of the text.

A date of birth is correct only when the text gives that date as the subject's birth. The date of the check, of a report, or of a document issue is not a birth date. An age at a date is two possible birth years (someone aged 45 in March 2019 was born in 1973 or 1974), and both years must be the ones claimed.

A place is correct only when the text ties that country to the subject as birth, residence, nationality, or a place they lived or worked. A country named only as the place of publication, or as someone else's country, is not.

For a search snippet, about_hit is true only if the snippet describes the same kind of listing as listing_phrase, in a country in hit_countries. Most snippets are about namesakes.

Agree only with facts the text supports. Do not add facts the claim omitted.

Reply with JSON only:
{"ok": true or false, "wrong": ["dob" or "country" or "name" or "birth_years" or "about_hit"], "issue": "one sentence, empty if ok"}"""

_WRONG_NAMES = {
    "dob": "dob", "date_of_birth": "dob", "birth_date": "dob", "birthdate": "dob",
    "country": "country", "countries": "country", "place": "country",
    "place_of_birth": "country", "location": "country",
    "name": "name",
    "birth_years": "birth_years", "birth_year": "birth_years", "years": "birth_years",
    "about_hit": "about_hit", "about": "about_hit",
}


def load_key():
    key = os.environ.get("GROK_API_KEY", "").strip()
    if key:
        return key
    path = os.path.join(ROOT, ".env")
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("GROK_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def clip(text, limit=2500):
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def cache_path():
    return os.environ.get("FACT_CHECK_CACHE") or CACHE


def _flag_off():
    return os.environ.get("FACT_CHECK", "").strip().lower() in ("0", "false", "no", "off")


def _flag_on():
    return os.environ.get("FACT_CHECK", "").strip().lower() in ("1", "true", "yes", "on")


def packet_digest(source_id, kind, text, claim):
    raw = json.dumps({"source_id": source_id, "kind": kind, "text": text, "claim": claim},
                     sort_keys=True, ensure_ascii=False, default=str)
    raw += "\n" + PROMPT
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def normalize_wrong(wrong):
    out = []
    for item in wrong or []:
        key = re.sub(r"[^a-z0-9]+", "_", str(item).strip().lower()).strip("_")
        out.append(_WRONG_NAMES.get(key, key))
    return out


def claim_stands(verdict, fields=None):
    """True when the claimed fields are not rejected. Missing or failed reviews fail open."""
    if not verdict or verdict.get("ok") is not False:
        return True
    wrong = set(normalize_wrong(verdict.get("wrong")))
    if not wrong:
        return False
    if not fields:
        return False
    return not (wrong & set(normalize_wrong(fields)))


def strip_kyc(parsed, verdict):
    """Drop the fields the model rejected. ok false with an empty wrong list drops all of them."""
    if not parsed or verdict.get("ok") is not False:
        return parsed
    wrong = set(normalize_wrong(verdict.get("wrong")))
    drop_all = not wrong
    out = dict(parsed)
    out["countries"] = list(parsed.get("countries") or [])
    if drop_all or "name" in wrong:
        out["name"] = None
    if drop_all or "dob" in wrong:
        out["dob"] = None
    if drop_all or "country" in wrong:
        out["country"] = None
        out["countries"] = []
    return out


def parse_reply(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    ok = data.get("ok")
    if isinstance(ok, str):
        ok = ok.strip().lower() in ("true", "yes", "ok")
    wrong = data.get("wrong") or []
    if isinstance(wrong, str):
        wrong = [wrong]
    if not isinstance(wrong, list):
        wrong = []
    return {"ok": bool(ok) if ok is not None else None, "wrong": [str(w) for w in wrong],
            "issue": (data.get("issue") or "").strip()}


def load_cache(path):
    found = {}
    if not os.path.isfile(path):
        return found
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            digest = row.get("digest")
            if digest:
                found[digest] = row
    return found


class Client:
    def __init__(self, key, model):
        self.key = key
        self.model = model
        self.json_mode = True
        self.lock = threading.Lock()

    def review(self, packet):
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 250,
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps(
                    {k: packet[k] for k in ("source_id", "kind", "text", "claim")},
                    ensure_ascii=False, default=str)},
            ],
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        request = urllib.request.Request(
            API_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                reply = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:400]
            if self.json_mode and error.code == 400 and "response_format" in detail:
                with self.lock:
                    self.json_mode = False
                return self.review(packet)
            raise ValueError("HTTP %s %s" % (error.code, detail))
        return parse_reply(reply["choices"][0]["message"]["content"])


class Checker:
    def __init__(self, key="", model=None, path=None):
        self.key = key
        self.model = model or os.environ.get("GROK_MODEL", DEFAULT_MODEL)
        self.path = path or cache_path()
        self.mode = COLLECT
        self.cache = load_cache(self.path)
        self.pending = {}

    def verdict(self, source_id, kind, text, claim):
        text = clip(text)
        claim = json.loads(json.dumps(claim, ensure_ascii=False, default=str))
        digest = packet_digest(source_id, kind, text, claim)
        packet = {"source_id": source_id, "kind": kind, "text": text, "claim": claim, "digest": digest}
        if self.mode == COLLECT:
            prior = self.cache.get(digest)
            if not prior or prior.get("ok") is None:
                self.pending[digest] = packet
            return {"ok": True, "wrong": []}
        prior = self.cache.get(digest)
        if prior and prior.get("ok") is not None:
            return {"ok": bool(prior["ok"]), "wrong": list(prior.get("wrong") or [])}
        return {"ok": True, "wrong": []}

    def flush(self):
        """Send queued readings that are not already cached. Returns how many were asked."""
        items = [(digest, packet) for digest, packet in sorted(self.pending.items())
                 if digest not in self.cache or self.cache[digest].get("ok") is None]
        self.pending.clear()
        if not items:
            return 0
        if not self.key:
            sys.stderr.write("GROK_API_KEY is not set; %d readings were not checked\n" % len(items))
            return 0
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        client = Client(self.key, self.model)
        lock = threading.Lock()
        handle = open(self.path, "a", encoding="utf-8", newline="\n")
        done = 0

        def one(item):
            digest, packet = item
            last = None
            for attempt in range(4):
                try:
                    reply = client.review(packet)
                    reply.update(source_id=packet["source_id"], kind=packet["kind"], digest=digest)
                    return digest, reply
                except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
                        TimeoutError, ValueError) as error:
                    last = error
                    time.sleep(1.5 * (attempt + 1))
            return digest, {"source_id": packet["source_id"], "kind": packet["kind"], "digest": digest,
                            "ok": None, "wrong": [], "issue": "check failed: %s" % last}

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [pool.submit(one, item) for item in items]
            for future in as_completed(futures):
                digest, reply = future.result()
                with lock:
                    handle.write(json.dumps(reply, ensure_ascii=False) + "\n")
                    handle.flush()
                    self.cache[digest] = reply
                done += 1
                if done % 25 == 0 or done == len(items):
                    sys.stderr.write("fact-checked %d/%d readings\n" % (done, len(items)))
        handle.close()
        return len(items)


def maybe_checker(live=True):
    """A live checker when the API key is set, otherwise None. FACT_CHECK=0 disables it."""
    if _flag_off():
        return None
    key = load_key()
    if not key:
        if _flag_on():
            sys.stderr.write("GROK_API_KEY is not set; fact checks skipped\n")
        return None
    checker = Checker(key if live else "", path=cache_path())
    if not live:
        checker.mode = ENFORCE
    return checker
