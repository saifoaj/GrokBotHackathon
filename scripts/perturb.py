"""Robustness test: surface changes that must not change any decision.

Usage: python3 scripts/perturb.py [data folder] [--only cat1,cat2] [--keep DIR]

Makes copies of the data folder with one kind of surface change each (and
one with all of them), runs system/run on every copy and on the original,
and reports how many decisions (decision + reason code) change. Target: 0.

Categories:
  dates        customer DOBs and KYC dates rewritten in another unambiguous
               format (ISO, "14 March 1988", "March 14, 1988", "Mar 14 1988");
               partial dates stay partial. Parsed here, independently of the
               system, using the bank's day-first convention.
  name_case    names re-cased (UPPER / lower / Title) and accents added or
               removed: customer names, KYC verified names, screened names,
               hit names and aliases.
  note_names   names inside KYC note text written in Title case instead of
               capitals (the unseen set may not use capitals).
  aliases      hit aliases reordered.
  note_noise   notes with extra whitespace and punctuation.
  countries    structured country fields swapped between names and ISO codes
               (customers, KYC documents, screened country, hit countries).
Every change is seeded, so the copies are the same on every run.
"""

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "system"))

import countries as country_table  # noqa: E402  (only to map names <-> ISO codes)

CATEGORIES = ["dates", "name_case", "note_names", "aliases", "note_noise", "countries"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MONTH_NUMBER = dict((m.lower(), i) for i, m in enumerate(MONTHS, 1))
MONTH_NUMBER.update((m[:3].lower(), i) for i, m in enumerate(MONTHS, 1))
ACCENT = {"a": "á", "e": "é", "i": "í", "o": "ö", "u": "ü", "n": "ñ", "c": "ç",
          "A": "Á", "E": "É", "I": "Í", "O": "Ö", "U": "Ü", "N": "Ñ", "C": "Ç"}
CAPS_KEEP = set(["DOB", "ID", "KYC", "UK", "US", "USA", "UAE", "EU", "N/A", "NA"])


# ---------------------------------------------------------------- helpers

def read(folder, name):
    with open(os.path.join(folder, name), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write(folder, name, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def rng_for(seed, key):
    digest = hashlib.sha256(("%s|%s" % (seed, key)).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def parse_bank_date(value):
    """Independent parser for the bank's date fields (day-first numeric)."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        return (int(match.group(3)), int(match.group(2)), int(match.group(1)))
    match = re.fullmatch(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", text)
    if match and match.group(2).lower() in MONTH_NUMBER:
        return (int(match.group(3)), MONTH_NUMBER[match.group(2).lower()], int(match.group(1)))
    match = re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{4})", text)
    if match and match.group(1).lower() in MONTH_NUMBER:
        return (int(match.group(3)), MONTH_NUMBER[match.group(1).lower()], int(match.group(2)))
    match = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if match:
        return (int(match.group(1)), int(match.group(2)), None)
    match = re.fullmatch(r"([A-Za-z]+) (\d{4})", text)
    if match and match.group(1).lower() in MONTH_NUMBER:
        return (int(match.group(2)), MONTH_NUMBER[match.group(1).lower()], None)
    match = re.fullmatch(r"(\d{4})", text)
    if match:
        return (int(match.group(1)), None, None)
    return None


def format_date(date, rng, original):
    year, month, day = date
    if month is None:
        return "%04d" % year
    if day is None:
        options = ["%04d-%02d" % (year, month), "%s %d" % (MONTHS[month - 1], year)]
    else:
        name = MONTHS[month - 1]
        options = ["%04d-%02d-%02d" % (year, month, day), "%d %s %d" % (day, name, year),
                   "%s %d, %d" % (name, day, year), "%s %d %d" % (name[:3], day, year)]
    options = [o for o in options if o != original.strip()] or options
    return rng.choice(options)


def rewrite_date(value, rng):
    parsed = parse_bank_date(value)
    if parsed is None:
        return value
    return format_date(parsed, rng, value)


def strip_accents(text):
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def recase(name, rng):
    if not isinstance(name, str) or not name.strip():
        return name
    style = rng.choice(["upper", "lower", "title"])
    text = strip_accents(name) if rng.random() < 0.5 else name
    if rng.random() < 0.5:
        chars = list(text)
        for index, ch in enumerate(chars):
            if ch in ACCENT and rng.random() < 0.3:
                chars[index] = ACCENT[ch]
        text = "".join(chars)
    if style == "upper":
        return text.upper()
    if style == "lower":
        return text.lower()
    return " ".join(word[:1].upper() + word[1:].lower() for word in text.split(" "))


def title_caps_words(text):
    def fix(match):
        word = match.group(0)
        if word in CAPS_KEEP or len(word) < 2:
            return word
        return word[:1] + word[1:].lower()
    return re.sub(r"[^\W\d_][^\W\d_'\-]*", lambda m: fix(m) if m.group(0).isupper() else m.group(0), text)


def noisy(text, rng):
    if not isinstance(text, str):
        return text
    words = text.split(" ")
    out = []
    for word in words:
        out.append(word)
        out.append("  " if rng.random() < 0.25 else " ")
    text = "".join(out[:-1])
    text = text.replace(", ", " ,  ") if rng.random() < 0.3 else text
    if text.endswith("."):
        text = text + rng.choice(["", ".", " .", ".."])
    else:
        text = text + rng.choice([".", " .", ""])
    return rng.choice(["", " ", "\t"]) + text + rng.choice(["", " ", "  "])


def swap_country(value, rng):
    """Country name -> ISO2 code, or code -> name. Unknown text unchanged."""
    if not isinstance(value, str) or not value.strip():
        return value
    tag = re.search(r"\s*(\[[^\]]*\])\s*$", value)
    core = value[:tag.start()] if tag else value
    suffix = (" " + tag.group(1)) if tag else ""
    found = country_table.lookup(core)
    if not found or len(found) != 1:
        return value
    name = found[0]
    code = None
    for line in country_table._TABLE.strip().splitlines():
        parts = line.split(";")
        if parts[0].strip() == name and parts[1].strip():
            code = parts[1].strip()
    if code is None:
        return value
    if core.strip().upper() == code:
        return name + suffix
    return (code if rng.random() < 0.7 else name.upper()) + suffix


# ---------------------------------------------------------------- perturbing

def perturb(source, target, categories, seed=7):
    os.makedirs(target)
    for name in sorted(os.listdir(source)):
        path = os.path.join(source, name)
        if os.path.isfile(path) and not name.endswith(".jsonl"):
            shutil.copyfile(path, os.path.join(target, name))

    customers = read(source, "customers.jsonl")
    for row in customers:
        rng = rng_for(seed, row["record_id"])
        if "dates" in categories:
            row["dob"] = rewrite_date(row.get("dob"), rng)
        if "name_case" in categories:
            row["full_name"] = recase(row.get("full_name"), rng)
            row["preferred_name"] = recase(row.get("preferred_name"), rng)
        if "countries" in categories:
            row["residence_country"] = swap_country(row.get("residence_country"), rng)
            row["nationality"] = swap_country(row.get("nationality"), rng)
    write(target, "customers.jsonl", customers)

    checks = read(source, "kyc_checks.jsonl")
    for row in checks:
        rng = rng_for(seed, row["check_id"])
        if "dates" in categories:
            row["verified_dob"] = rewrite_date(row.get("verified_dob"), rng)
            row["checked_at"] = rewrite_date(row.get("checked_at"), rng)
        if "name_case" in categories:
            row["verified_name"] = recase(row.get("verified_name"), rng)
        if "countries" in categories:
            row["document_country"] = swap_country(row.get("document_country"), rng)
    write(target, "kyc_checks.jsonl", checks)

    notes = read(source, "notes.jsonl")
    for row in notes:
        rng = rng_for(seed, row["note_id"])
        if "note_names" in categories and row.get("channel") == "kyc":
            row["text"] = title_caps_words(row.get("text") or "")
        if "note_noise" in categories:
            row["text"] = noisy(row.get("text"), rng)
    write(target, "notes.jsonl", notes)

    alerts = read(source, "alerts.jsonl")
    for row in alerts:
        rng = rng_for(seed, row["alert_id"])
        customer = row.get("customer") or {}
        if "name_case" in categories:
            customer["name"] = recase(customer.get("name"), rng)
        if "countries" in categories:
            customer["country"] = swap_country(customer.get("country"), rng)
    write(target, "alerts.jsonl", alerts)

    hits = read(source, "hits.jsonl")
    for row in hits:
        rng = rng_for(seed, row["hit_id"])
        if "name_case" in categories:
            for field in ("name", "first_name", "middle_name", "last_name", "best_matching_name"):
                row[field] = recase(row.get(field), rng)
            row["aliases"] = [recase(a, rng) for a in row.get("aliases") or []]
        if "aliases" in categories:
            aliases = list(row.get("aliases") or [])
            rng.shuffle(aliases)
            row["aliases"] = aliases
        if "countries" in categories:
            for field in ("countries", "nationality", "citizenship"):
                row[field] = [swap_country(v, rng) for v in row.get(field) or []]
    write(target, "hits.jsonl", hits)

    for name in ("accounts.jsonl", "onboarding.jsonl", "search_results.jsonl", "practice_labels.jsonl"):
        if os.path.isfile(os.path.join(source, name)):
            shutil.copyfile(os.path.join(source, name), os.path.join(target, name))


# ---------------------------------------------------------------- checking

def run_system(data, output):
    subprocess.check_call([os.path.join(ROOT, "system", "run"), data, output],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(output, encoding="utf-8") as handle:
        return dict((row["hit_id"], row) for row in (json.loads(line) for line in handle if line.strip()))


def compare(base, other):
    changed = []
    for hit_id in sorted(base):
        a, b = base[hit_id], other.get(hit_id, {})
        if (a["decision"], a["reason_code"]) != (b.get("decision"), b.get("reason_code")):
            changed.append((hit_id, a, b))
    records = sum(1 for h in base if base[h]["customer_record_id"] != other.get(h, {}).get("customer_record_id"))
    return changed, records


def main(argv):
    args = argv[1:]
    folder = os.path.join(ROOT, "dev")
    only, keep = None, None
    while args:
        arg = args.pop(0)
        if arg == "--only":
            only = args.pop(0).split(",")
        elif arg == "--keep":
            keep = args.pop(0)
        else:
            folder = arg
    work = keep or tempfile.mkdtemp(prefix="perturb.")
    if keep and os.path.isdir(keep):
        shutil.rmtree(keep)
        os.makedirs(keep)
    try:
        base = run_system(folder, os.path.join(work, "base.jsonl"))
        runs = [[c] for c in (only or CATEGORIES)] + ([only or CATEGORIES] if len(only or CATEGORIES) > 1 else [])
        total_changes = 0
        for categories in runs:
            label = "+".join(categories) if len(categories) < len(CATEGORIES) else "ALL"
            target = os.path.join(work, "data_" + label.replace("+", "_"))
            perturb(folder, target, set(categories))
            other = run_system(target, os.path.join(work, "out_%s.jsonl" % label.replace("+", "_")))
            changed, records = compare(base, other)
            total_changes += len(changed) if label != "ALL" else 0
            print("%-12s %3d decisions changed, %3d customer records changed" % (label, len(changed), records))
            for hit_id, a, b in changed[:8]:
                print("    %s: %s %s -> %s %s" % (hit_id, a["decision"], a["reason_code"],
                                                 b.get("decision"), b.get("reason_code")))
            if len(changed) > 8:
                print("    ... %d more" % (len(changed) - 8))
        return 0
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
