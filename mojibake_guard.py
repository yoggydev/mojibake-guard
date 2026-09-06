#!/usr/bin/env python3
"""
mojibake-guard — catch silent non-ASCII corruption in files an AI agent writes.

Not a dictionary. It compares what was *intended* against what actually landed
on disk, at the codepoint level, so it is language-agnostic.

Failure modes it was built from (all reported in the wild):
  ASCII substitution   ae -> ae, oe -> oe, ue -> ue, ss -> ss   (de, tr)
  silent drop          Verzoegerung -> Verzogerung              (es, fr, de)
  replacement char     nordic vowels -> U+FFFD                  (Windows)
  NUL bytes            box-drawing -> \\x00                      (Windows)
  CP932 byte-swallow   the byte after Japanese text disappears  (ja)
  UTF-8 read as Latin-1 mojibake                                (any)

Modes
  hook       Claude Code PostToolUse hook; reads hook JSON on stdin
  compare    --intended FILE --actual FILE
  scan       --scan PATH...   (no intended version needed)
  selftest   --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

__version__ = "0.1.0"

REPLACEMENT = "�"

# Expansions seen in the wild. Used only to *explain* a loss that codepoint
# comparison already proved, never on its own — so writing "ae" on purpose can
# not trigger anything.
ASCII_EXPANSION = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
    "å": "a", "Å": "A", "ø": "o", "Ø": "O",
    "æ": "ae", "Æ": "Ae",
    "é": "e", "è": "e", "ê": "e", "ë": "e",
    "á": "a", "à": "a", "â": "a",
    "í": "i", "ì": "i", "î": "i",
    "ó": "o", "ò": "o", "ô": "o",
    "ú": "u", "ù": "u", "û": "u",
    "ñ": "n", "ç": "c",
    "ı": "i", "ş": "s", "ğ": "g",
    "İ": "I", "Ş": "S", "Ğ": "G",
}

# ASCII bytes >= 0x40 that vanish after Japanese text when a CP932 round trip
# eats the trail byte. Measured on 545 Japanese chars x 95 ASCII symbols:
# 63 of 95 symbols disappear; the quote characters never do.
CP932_FRAGILE = set("\\{}[]|~^@`")

# UTF-8 that was decoded as Latin-1/CP1252 has a shape, not a vocabulary:
# a lead character in U+00C2..U+00F4 followed by continuation characters in
# U+0080..U+00BF. No word list, so every language is covered at once.
# A run only counts if it really decodes back as valid UTF-8, which is what
# keeps legitimate Latin-1 text (Portuguese "\u00c3o", French "\u00c2\u00ab") clean.
MOJIBAKE_RUN = re.compile(
    "[\u00c2-\u00df][\u0080-\u00bf]"
    "|[\u00e0-\u00ef][\u0080-\u00bf]{2}"
    "|[\u00f0-\u00f4][\u0080-\u00bf]{3}"
)

# C1 control characters. Real text does not contain these; CP1252 text read as
# Latin-1 does (curly quotes, en/em dashes and the euro sign land in here).
C1_CONTROLS = re.compile("[\u0080-\u009f]")

SEV_BLOCK = "block"
SEV_INFO = "info"


@dataclass
class Finding:
    code: str
    detail: str
    samples: list = field(default_factory=list)
    severity: str = SEV_BLOCK

    def render(self) -> str:
        head = "[%s] %s" % (self.code, self.detail)
        if not self.samples:
            return head
        return head + "\n" + "\n".join("    %s" % s for s in self.samples[:5] if s)


def _nonascii_counter(text: str) -> Counter:
    return Counter(ch for ch in text if ord(ch) > 127)


def _context(text: str, ch: str, width: int = 24) -> str:
    i = text.find(ch)
    if i < 0:
        return ""
    return repr(text[max(0, i - width):i + width])


def _name(ch: str) -> str:
    try:
        return unicodedata.name(ch)
    except ValueError:
        return "U+%04X" % ord(ch)


def analyse(intended: str, actual: str) -> list:
    """Compare intended text against what is actually on disk."""
    out = []

    # D1  replacement characters that were not asked for
    added_fffd = actual.count(REPLACEMENT) - intended.count(REPLACEMENT)
    if added_fffd > 0:
        out.append(Finding(
            "REPLACEMENT_CHAR",
            "%d U+FFFD appeared that were not in the intended text" % added_fffd,
            [_context(actual, REPLACEMENT)],
        ))

    # D2  NUL bytes in what should be text
    added_nul = actual.count("\x00") - intended.count("\x00")
    if added_nul > 0:
        out.append(Finding(
            "NUL_BYTE",
            "%d NUL byte(s) appeared in a text file" % added_nul,
            [_context(actual, "\x00")],
        ))

    # D3  UTF-8 that came back decoded as Latin-1
    new_runs = len(latin1_mojibake_runs(actual)) - len(latin1_mojibake_runs(intended))
    if new_runs > 0:
        pairs = latin1_mojibake_runs(actual)[:4]
        out.append(Finding(
            "LATIN1_MOJIBAKE",
            "%d new run(s) of UTF-8 read as Latin-1: %s" % (
                new_runs, ", ".join("%r should be %r" % p for p in pairs)),
            [_context(actual, pairs[0][0])],
        ))

    # D4  non-ASCII codepoints that went missing  <- the core. exact, no guessing
    lost = _nonascii_counter(intended) - _nonascii_counter(actual)
    if lost:
        # Unicode normalisation is not corruption. If every missing codepoint
        # comes back once both sides are NFC, report it and do not block.
        n_lost = (_nonascii_counter(unicodedata.normalize("NFC", intended))
                  - _nonascii_counter(unicodedata.normalize("NFC", actual)))
        if not n_lost:
            out.append(Finding(
                "UNICODE_NORMALIZED",
                "text was re-normalised (NFC/NFD) but no character was lost",
                severity=SEV_INFO,
            ))
        else:
            lost = n_lost
            bits = ["%r x%d (%s)" % (ch, n, _name(ch)) for ch, n in lost.most_common(8)]
            out.append(Finding(
                "NONASCII_LOST",
                "%d non-ASCII character(s) disappeared: %s"
                % (sum(lost.values()), ", ".join(bits)),
                [_context(intended, ch) for ch, _ in lost.most_common(3)],
            ))

            # D4  explain it when it looks like ASCII substitution
            subs = []
            for ch in lost:
                exp = ASCII_EXPANSION.get(ch)
                if exp and actual.count(exp) > intended.count(exp):
                    subs.append("%s -> %s" % (ch, exp))
            if subs:
                out.append(Finding(
                    "ASCII_SUBSTITUTION",
                    "looks like ASCII substitution: " + ", ".join(sorted(subs)),
                ))

            # D5  or like a plain drop (gone, nothing put in its place)
            drops = [ch for ch in lost
                     if not (ASCII_EXPANSION.get(ch)
                             and actual.count(ASCII_EXPANSION[ch])
                             > intended.count(ASCII_EXPANSION[ch]))]
            if drops:
                out.append(Finding(
                    "SILENT_DROP",
                    "dropped with no substitute: "
                    + ", ".join(repr(c) for c in sorted(drops)[:8]),
                ))

    # D6  CP932 byte-swallow signature: fragile ASCII vanishing next to CJK.
    #     Only fires when the count actually fell, so normal text is untouched.
    swallowed = []
    for ch in sorted(CP932_FRAGILE):
        d = intended.count(ch) - actual.count(ch)
        if d > 0:
            swallowed.append("%r x%d" % (ch, d))
    if swallowed:
        out.append(Finding(
            "CP932_BYTE_SWALLOW",
            "ASCII bytes >= 0x40 disappeared (CP932 eats the byte after Japanese "
            "text): " + ", ".join(swallowed),
            ["a vanished { or } surfaces later as an unmatched brace elsewhere"],
        ))

    return out


def scan_text(text: str) -> list:
    """Corruption visible without an intended version. Conservative on purpose."""
    out = []

    n = text.count(REPLACEMENT)
    if n:
        out.append(Finding("REPLACEMENT_CHAR", "%d U+FFFD in the file" % n,
                           [_context(text, REPLACEMENT)]))

    n = text.count("\x00")
    if n:
        out.append(Finding("NUL_BYTE", "%d NUL byte(s) in a text file" % n,
                           [_context(text, "\x00")]))

    runs = latin1_mojibake_runs(text)
    if runs:
        shown = ", ".join("%r -> %r" % (bad, good) for bad, good in runs[:6])
        out.append(Finding(
            "LATIN1_MOJIBAKE",
            "%d run(s) that only occur when UTF-8 is read as Latin-1: %s"
            % (len(runs), shown),
            [_context(text, runs[0][0])],
        ))

    stray = C1_CONTROLS.findall(text)
    if stray and not runs:
        out.append(Finding(
            "C1_CONTROL",
            "%d C1 control character(s) (U+0080-U+009F) in a text file: %s"
            % (len(stray), ", ".join(sorted({"U+%04X" % ord(c) for c in stray})[:8])),
            [_context(text, stray[0])],
        ))

    return out


def latin1_mojibake_runs(text: str) -> list:
    """Runs that decode back to something sensible: proof, not a guess."""
    found = []
    for m in MOJIBAKE_RUN.finditer(text):
        run = m.group(0)
        try:
            fixed = run.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        # A one-character result that is itself C1 is not a real recovery.
        if all(0x80 <= ord(c) <= 0x9F for c in fixed):
            continue
        found.append((run, fixed))
    return found


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    # Decode permissively so corruption is visible rather than fatal.
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- hook mode

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def intended_from_tool_input(tool_name: str, tool_input: dict) -> str:
    if tool_name == "Write":
        return tool_input.get("content") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string") or ""
    if tool_name == "MultiEdit":
        return "".join((e.get("new_string") or "")
                       for e in tool_input.get("edits") or [])
    if tool_name == "NotebookEdit":
        return tool_input.get("new_source") or ""
    return ""


HINT = (
    "The characters above were in the text being written but are not in the file.\n"
    "Write the file again preserving them. On Windows, writing UTF-8 with a BOM\n"
    "prevents this class of loss (measured: 0 of 545 Japanese characters broken)."
)


def run_hook() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never break the session on malformed input

    tool_name = payload.get("tool_name") or ""
    if tool_name not in WRITE_TOOLS:
        return 0

    tool_input = payload.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not path or not os.path.isfile(path):
        return 0

    intended = intended_from_tool_input(tool_name, tool_input)
    if not intended.strip():
        return 0
    if not any(ord(c) > 127 for c in intended):
        return 0  # nothing non-ASCII was being written

    findings = analyse(intended, read_text(path))
    blocking = [f for f in findings if f.severity == SEV_BLOCK]
    if not blocking:
        return 0

    sys.stderr.write("\n".join(
        ["mojibake-guard: non-ASCII corruption detected in %s" % path, ""]
        + [f.render() for f in blocking]
        + ["", HINT]
    ) + "\n")
    return 2  # block, and show the reason to the agent


# ---------------------------------------------------------------- cli

def run_scan(paths: list) -> int:
    bad = 0
    for p in paths:
        try:
            findings = scan_text(read_text(p))
        except (OSError, UnicodeError) as exc:
            print("%s: unreadable (%s)" % (p, exc))
            continue
        if findings:
            bad += 1
            print(p)
            for f in findings:
                print("  " + f.render().replace("\n", "\n  "))
    print("scanned %d file(s), %d with findings" % (len(paths), bad))
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="mojibake-guard", add_help=True)
    ap.add_argument("--intended")
    ap.add_argument("--actual")
    ap.add_argument("--scan", nargs="+", metavar="PATH")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args()

    if args.selftest:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from test_corpus import run_corpus
        return run_corpus()

    if args.scan:
        return run_scan(args.scan)

    if args.intended and args.actual:
        findings = analyse(read_text(args.intended), read_text(args.actual))
        if not findings:
            print("clean")
            return 0
        for f in findings:
            print(f.render())
        return 1 if any(f.severity == SEV_BLOCK for f in findings) else 0

    return run_hook()


if __name__ == "__main__":
    sys.exit(main())
