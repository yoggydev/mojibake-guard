#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_session_mode.py — offline checks for repro.py --mode session.

No `claude` process, no API calls, no cost. `subprocess.run` is replaced by a
scripted fake that writes whatever a scenario says the model wrote, so the
thing under test here is the CLASSIFIER, not the model.

Why this file exists at all: session mode introduced a failure class the
single-request modes never had. A turn can simply not land — the model answers
without calling Edit, or edits the wrong line. The required words are then
missing from the file, and a codepoint comparison cannot tell "the model
dropped the umlaut" from "the model never wrote the word". Counting the second
one as a reproduction would manufacture exactly the result we want to see,
which is the worst kind of bug a measuring instrument can have.

So each scenario below pins one verdict:

  clean         every turn edits correctly                  -> OK
  ascii_sub     turn 4 writes "Verzoegerung"                 -> CORRUPT at t4
  skipped       turn 3 does nothing at all                   -> that turn SKIPPED,
                                                                run still OK
  cross_turn    turn 5 strips the diacritics off turn 2's
                line while every line passes on its own      -> CORRUPT,
                                                                cross_turn_damage
  no_edits      nothing ever lands                           -> NOFILE

Run:  python3 test_session_mode.py
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import repro  # noqa: E402

REAL_RUN = subprocess.run

ASCII_EXPAND = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
                "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}


def ascii_substitute(text: str) -> str:
    for ch, rep in ASCII_EXPAND.items():
        text = text.replace(ch, rep)
    return text


def strip_marks(text: str) -> str:
    """Drop combining marks: 'Verzögerung' -> 'Verzogerung'. The silent-drop
    shape reported by iBoarder in #14131."""
    d = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(
        c for c in d if not unicodedata.combining(c)))


class Fake:
    """Stands in for `claude`. Writes the file the way a scenario dictates."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.calls = 0
        self.resumed = []

    def __call__(self, argv, input=None, cwd=None, **kw):
        self.calls += 1
        if "--resume" in argv:
            self.resumed.append(argv[argv.index("--resume") + 1])

        marker = re.search(r"- (TURN-\d+): TODO", input).group(1)
        turn = int(marker.split("-")[1])
        # The words block is everything between the blank line after the colon
        # and the "Use the Edit tool" line.
        words = re.search(r"them:\n\n(.+?)\n\nUse the Edit tool",
                          input, re.S).group(1).strip()

        path = os.path.join(cwd, repro.SESSION_FILE)
        with open(path, encoding="utf-8") as f:
            text = f.read()

        sentence = "%s wurde geplant." % words
        if self.scenario == "ascii_sub" and turn == 4:
            sentence = ascii_substitute(sentence)
        if self.scenario == "skipped" and turn == 3:
            return self._reply(turn)          # answers, edits nothing
        if self.scenario == "no_edits":
            return self._reply(turn)

        text = text.replace("- %s: TODO" % marker,
                            "- %s: %s" % (marker, sentence))

        if self.scenario == "seed_damage" and turn == 4:
            # The turn writes its OWN line correctly, but mangles the German
            # prose that was already in the file before the session started.
            # Per-turn checks all pass; only the seed check can see this.
            out = []
            for ln in text.splitlines():
                if not ln.startswith("- TURN-") and not ln.startswith("#"):
                    ln = strip_marks(ln)
                out.append(ln)
            text = "\n".join(out) + "\n"

        if self.scenario == "cross_turn" and turn == 5:
            # Collateral damage: re-editing the file mangles a line that an
            # earlier turn already wrote correctly.
            out = []
            for line in text.splitlines():
                if line.startswith("- TURN-02:"):
                    line = strip_marks(line)
                out.append(line)
            text = "\n".join(out) + "\n"

        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return self._reply(turn)

    def _reply(self, turn):
        payload = {
            "result": "DONE",
            "session_id": "fake-session-0001",
            "total_cost_usd": 0.0,
            "modelUsage": {"claude-fake-1": {"outputTokens": 40}},
        }
        return subprocess.CompletedProcess(
            args=["claude"], returncode=0,
            stdout=json.dumps(payload), stderr="")


def run(scenario, turns=6, per_turn=3, prose_seed=False):
    fake = Fake(scenario)
    subprocess.run = fake
    try:
        rec = repro.one_session_run("de", "", 60, False, turns, per_turn,
                                    prose_seed)
    finally:
        subprocess.run = REAL_RUN
    return rec, fake


FAILURES = []


def check(name, got, want):
    ok = got == want
    print("  %-34s %-12s %s" % (name, "ok" if ok else "FAIL",
                                "" if ok else "got %r, want %r" % (got, want)))
    if not ok:
        FAILURES.append(name)


def main() -> int:
    print("scenario: clean")
    rec, fake = run("clean")
    check("verdict", rec["verdict"], "OK")
    check("turns landed", rec["turns_landed"], 6)
    check("codes", rec["codes"], [])
    check("one thread", rec["one_thread"], True)
    check("resumed after turn 1", len(fake.resumed), 5)

    print("scenario: ascii_sub (turn 4)")
    rec, _ = run("ascii_sub")
    check("verdict", rec["verdict"], "CORRUPT")
    check("first corrupt turn", rec.get("first_corrupt_turn"), 4)
    check("ASCII_SUBSTITUTION seen",
          "ASCII_SUBSTITUTION" in rec["codes"], True)
    check("turns landed", rec["turns_landed"], 6)

    print("scenario: skipped (turn 3 does nothing)")
    rec, _ = run("skipped")
    check("verdict", rec["verdict"], "OK")
    check("turn 3 SKIPPED", rec["turns"][2]["verdict"], "SKIPPED")
    check("turns landed", rec["turns_landed"], 5)
    check("no corruption invented", rec["codes"], [])

    print("scenario: cross_turn (turn 5 mangles turn 2's line)")
    rec, _ = run("cross_turn")
    check("verdict", rec["verdict"], "CORRUPT")
    check("cross_turn_damage", rec.get("cross_turn_damage"), True)
    check("no per-turn corruption", rec.get("first_corrupt_turn"), None)
    check("code is prefixed",
          any(c.startswith("CROSS_TURN_") for c in rec["codes"]), True)

    print("scenario: no_edits")
    rec, _ = run("no_edits")
    check("verdict", rec["verdict"], "NOFILE")
    check("turns landed", rec["turns_landed"], 0)

    # The pure-ASCII seed cannot see damage to text that was already on disk,
    # because there is no non-ASCII on disk to damage. --prose-seed is the
    # whole point of these two scenarios.
    print("prose seed: clean session leaves the German prose intact")
    rec, _ = run("clean", prose_seed=True)
    check("verdict", rec["verdict"], "OK")
    check("seed_damaged_at", rec.get("seed_damaged_at"), None)
    check("every turn reports seed_ok",
          all(t.get("seed_ok") for t in rec["turns"]), True)

    print("prose seed: turn 4 mangles the pre-existing prose")
    rec, _ = run("seed_damage", prose_seed=True)
    check("verdict", rec["verdict"], "CORRUPT")
    check("seed_damaged_at", rec.get("seed_damaged_at"), 4)
    check("no per-turn corruption", rec.get("first_corrupt_turn"), None)
    check("code is prefixed",
          any(c.startswith("SEED_") for c in rec["codes"]), True)
    check("turn 3 was still clean", rec["turns"][2].get("seed_ok"), True)

    print("prose seed: the ASCII seed would have missed it")
    rec, _ = run("seed_damage", prose_seed=False)
    check("ascii seed sees nothing", rec["verdict"], "OK")
    check("seed_damaged_at not set", rec.get("seed_damaged_at"), None)

    print("word splitting")
    chunks = repro.turn_words("de", 6, 3)
    check("turn count", len(chunks), 6)
    check("words per turn", {len(c) for c in chunks}, {3})
    check("no repeats within 20-word pool",
          len({w for c in chunks for w in c}), 18)
    check("cycles when asked for more than the pool",
          len(repro.turn_words("de", 10, 3)), 10)

    print("seed file is pure ASCII")
    seed = repro.session_seed(6)
    check("no non-ASCII in seed", any(ord(c) > 127 for c in seed), False)
    check("one line per turn", seed.count("TODO"), 6)

    print("ledger prompt renders")
    check("session prompt has no stray field",
          "{" not in repro.ledger_prompt("session"), True)
    for m in repro.PROMPTS:
        check("prompt renders: " + m, "{" not in repro.ledger_prompt(m), True)

    # Regression: ja-JP Windows console, 2026-09-13. --render wrote MATRIX.md
    # correctly and then died printing it, because U+2013 has no CP932
    # encoding. The same crash would have hit the final result dump, which
    # carries non-ASCII ONLY when a run was corrupt -- so the harness would
    # have failed at the exact moment it first caught something.
    print("console encoding (the CP932 crash)")
    strict = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")
    try:
        strict.write("–")
        strict.flush()
        rejected = False
    except UnicodeEncodeError:
        rejected = True
    check("a real cp932 stream rejects U+2013", rejected, True)

    pinned = io.TextIOWrapper(io.BytesIO(), encoding="cp932", errors="strict")
    pinned.reconfigure(encoding="utf-8", errors="replace")
    corrupt_like = {"verdict": "CORRUPT",
                    "detail": "1 non-ASCII character(s) disappeared: "
                              "'ö' x1 (LATIN SMALL LETTER O WITH DIAERESIS)",
                    "actual": "- TURN-04: Verzoegerung 文字化け – ok"}
    try:
        pinned.write("– " + json.dumps(corrupt_like, ensure_ascii=False))
        pinned.flush()
        survived = True
    except UnicodeEncodeError:
        survived = False
    check("after pinning, a CORRUPT dump survives", survived, True)
    check("repro pins stdout on startup",
          callable(getattr(repro, "pin_stdout_utf8", None)), True)

    print()
    if FAILURES:
        print("FAILED: %s" % ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
