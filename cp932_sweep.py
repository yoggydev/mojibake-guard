#!/usr/bin/env python3
"""
cp932_sweep.py — an actually exhaustive sweep, not a claim about one.

The question: when a Japanese character is followed by a printable ASCII
character, and the pair goes through a broken encoding path, does the ASCII
character survive?

The space is small enough to test completely, so it is tested completely:

    every character CP932 encodes as two bytes      (enumerated, not sampled)
  x every printable ASCII character 0x20-0x7E       (95, all of them)
  x every corruption path listed below              (defined in code)

Nothing is extrapolated. Every cell is executed. The output is one CSV row per
failing cell plus a summary, so the count in the summary is the count of rows
that were actually run — you can check it by counting the file.

Paths are real, named round trips. No path is a guess about what some tool
"probably" does: each one is a concrete encode/decode pair you can repeat in
three lines of Python.

Usage
  python cp932_sweep.py                     # full sweep, writes results/cp932/
  python cp932_sweep.py --out somewhere/
  python cp932_sweep.py --limit-chars 100   # quick smoke run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import sys
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone

ASCII_PRINTABLE = [chr(c) for c in range(0x20, 0x7F)]  # exactly 95


def cp932_double_byte_chars():
    """Every character CP932 stores in two bytes. Enumerated, not sampled."""
    out = []
    for lead in range(0x81, 0xFD):
        for trail in range(0x40, 0xFD):
            raw = bytes((lead, trail))
            try:
                ch = raw.decode("cp932")
            except UnicodeDecodeError:
                continue
            if len(ch) == 1 and ch.encode("cp932", errors="ignore") == raw:
                out.append(ch)
    return out


# --------------------------------------------------------------- the paths
#
# Each path takes the two-character string and returns what comes out the
# other side. A path is a concrete pair of codec operations, nothing more.

def path_cp932_read_as_utf8_ignore(s):
    return s.encode("cp932").decode("utf-8", errors="ignore")


def path_cp932_read_as_utf8_replace(s):
    return s.encode("cp932").decode("utf-8", errors="replace")


def path_utf8_read_as_cp932_ignore(s):
    return s.encode("utf-8").decode("cp932", errors="ignore")


def path_cp932_read_as_latin1_then_utf8_ignore(s):
    return s.encode("cp932").decode("latin-1").encode("utf-8").decode("utf-8")


def path_utf8_trail_byte_lost(s):
    """The trail byte of the Japanese character goes missing, so the decoder
    reaches into the next character to complete the sequence."""
    raw = bytearray(s.encode("cp932"))
    if len(raw) >= 2:
        del raw[1]
    return bytes(raw).decode("cp932", errors="ignore")


def path_cp932_lead_byte_lost(s):
    raw = bytearray(s.encode("cp932"))
    if raw:
        del raw[0]
    return bytes(raw).decode("cp932", errors="ignore")


PATHS = OrderedDict([
    ("cp932_bytes_read_as_utf8_ignore", path_cp932_read_as_utf8_ignore),
    ("cp932_bytes_read_as_utf8_replace", path_cp932_read_as_utf8_replace),
    ("utf8_bytes_read_as_cp932_ignore", path_utf8_read_as_cp932_ignore),
    ("cp932_via_latin1_to_utf8", path_cp932_read_as_latin1_then_utf8_ignore),
    ("cp932_trail_byte_lost", path_utf8_trail_byte_lost),
    ("cp932_lead_byte_lost", path_cp932_lead_byte_lost),
])


def main() -> int:
    ap = argparse.ArgumentParser(prog="cp932_sweep.py")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit-chars", type=int, default=0,
                    help="use only the first N Japanese characters (smoke run)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    outdir = args.out or os.path.join(here, "results", "cp932")
    os.makedirs(outdir, exist_ok=True)

    chars = cp932_double_byte_chars()
    if args.limit_chars:
        chars = chars[: args.limit_chars]

    total_cells = len(chars) * len(ASCII_PRINTABLE) * len(PATHS)
    print("characters (CP932 double-byte) : %s" % format(len(chars), ","))
    print("ascii printable                : %d" % len(ASCII_PRINTABLE))
    print("paths                          : %d" % len(PATHS))
    print("cells to execute               : %s" % format(total_cells, ","))
    print()

    t0 = time.time()
    executed = 0
    lost_rows = []
    per_path = OrderedDict((name, Counter()) for name in PATHS)
    ascii_loss = OrderedDict((name, Counter()) for name in PATHS)

    for ch in chars:
        for a in ASCII_PRINTABLE:
            s = ch + a
            for name, fn in PATHS.items():
                executed += 1
                try:
                    got = fn(s)
                except Exception:
                    per_path[name]["error"] += 1
                    continue
                kept_ascii = a in got
                kept_char = ch in got
                if kept_ascii:
                    per_path[name]["ascii_survived"] += 1
                else:
                    per_path[name]["ascii_lost"] += 1
                    ascii_loss[name][a] += 1
                    lost_rows.append({
                        "path": name,
                        "char": ch,
                        "char_codepoint": "U+%04X" % ord(ch),
                        "char_cp932_bytes": ch.encode("cp932").hex(),
                        "ascii": a,
                        "ascii_byte": "0x%02X" % ord(a),
                        "output": got,
                    })
                per_path[name]["cjk_survived" if kept_char else "cjk_lost"] += 1

    seconds = time.time() - t0

    csv_path = os.path.join(outdir, "ascii-lost.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "char", "char_codepoint",
                                          "char_cp932_bytes", "ascii",
                                          "ascii_byte", "output"])
        w.writeheader()
        for row in lost_rows:
            w.writerow(row)

    summary = {
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platform": "%s %s" % (platform.system(), platform.release()),
        "python": platform.python_version(),
        "japanese_characters": len(chars),
        "ascii_printable": len(ASCII_PRINTABLE),
        "paths": list(PATHS),
        "combinations_char_x_ascii": len(chars) * len(ASCII_PRINTABLE),
        "cells_executed": executed,
        "seconds": round(seconds, 1),
        "rows_where_ascii_was_lost": len(lost_rows),
        "per_path": {k: dict(v) for k, v in per_path.items()},
        "ascii_chars_ever_lost": {
            k: sorted(v, key=lambda c: ord(c)) for k, v in ascii_loss.items() if v
        },
    }
    json_path = os.path.join(outdir, "summary.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("executed %s cells in %.1fs" % (format(executed, ","), seconds))
    print("combinations (char x ascii)   : %s"
          % format(summary["combinations_char_x_ascii"], ","))
    print()
    for name in PATHS:
        c = per_path[name]
        lost = c.get("ascii_lost", 0)
        tot = lost + c.get("ascii_survived", 0)
        pct = (100.0 * lost / tot) if tot else 0.0
        n_ascii = len(ascii_loss[name])
        print("  %-34s ascii lost %8s / %-8s (%6.2f%%)  distinct ascii: %d/95"
              % (name, format(lost, ","), format(tot, ","), pct, n_ascii))
    print()
    print("rows written : %s" % csv_path)
    print("summary      : %s" % json_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
