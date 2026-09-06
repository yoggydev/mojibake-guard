# mojibake-guard

A Claude Code hook that refuses a write when non-ASCII characters silently
disappear from the file.

```
[NONASCII_LOST] 7 non-ASCII character(s) disappeared: 'ö' x2, 'ü' x2, 'ß' x2, 'ä' x1
    'ation\n\nDie Verzögerung führt zu größeren Ausfäll'
[ASCII_SUBSTITUTION] looks like ASCII substitution: ß -> ss, ä -> ae, ö -> oe, ü -> ue
```

Exit code 2, the write is reported back to the agent, and the agent gets told
what to fix. No dictionary, no word list, no language configuration.

## Why

Agents write `Verzogerung` where you asked for `Verzögerung`, `Strasse` where
you wrote `Straße`, `?` where you wrote `å`. Nothing errors. The diff looks
fine at a glance. You find it in review, or in production.

Rules in `CLAUDE.md` do not fix this — several people report placing the same
rule at project, profile and root level and still getting the substitution back
the next session. A rule is a request to a model. This is a check on the bytes.

## How it works

It compares **what was asked for** against **what is on disk**, as a multiset of
codepoints. If a character was in the text being written and is not in the file,
that is a fact, not a heuristic — and it holds for every language at once,
because nothing in the comparison knows what a language is.

| code | what it means |
|---|---|
| `NONASCII_LOST` | a codepoint you wrote is not in the file |
| `ASCII_SUBSTITUTION` | …and an ASCII expansion appeared in its place (`ä`→`ae`) |
| `SILENT_DROP` | …and nothing appeared in its place |
| `REPLACEMENT_CHAR` | U+FFFD appeared that you did not write |
| `NUL_BYTE` | NUL bytes appeared in a text file |
| `LATIN1_MOJIBAKE` | UTF-8 was read back as Latin-1 (`Ã¤`), verified by re-decoding |
| `CP932_BYTE_SWALLOW` | an ASCII byte ≥ 0x40 vanished after Japanese text |
| `C1_CONTROL` | U+0080–U+009F in a text file |
| `UNICODE_NORMALIZED` | NFC/NFD changed but nothing was lost — reported, never blocks |

The word list in `ASCII_EXPANSION` never triggers anything on its own. It only
labels a loss that the codepoint comparison already proved, so writing `Strasse`
or `Goethe` on purpose is invisible to it.

## Measured

`python3 mojibake_guard.py --selftest`. Every number below is counted by that
command, not estimated. The synthetic corpus is fixed. The real-file corpus is
whatever non-ASCII UTF-8 text the machine running the suite happens to have —
Django's `.po` catalogues across ~100 locales, Babel's locale data, the Python
standard library, installed packages, local Markdown — so it differs per
machine, and the report prints the count. If it prints 0, only the synthetic
corpus was measured and the numbers below do not apply to that run.

| machine | real files | compare clean | compare FP | compare broken | missed | scan FP |
|---|---:|---:|---:|---:|---:|---:|
| Linux 6.18, Python 3.11 | 1,500 | 15,000 | **0** | 8,282 | **0** | 8 (0.533%) |
| Windows 11, Python 3.12 | 924 | 9,240 | **0** | 5,072 | **0** | 6 (0.649%) |

Both machines also run the same fixed synthetic corpus: 300 clean pairs, 0 false
positives; 179 corrupted pairs, 0 missed. The detailed breakdown below is from
the Linux run.

```
COMPARE MODE (hook: intended vs what landed on disk)
  clean  synthetic      300 pairs   false positives    0   (0.000%)
  clean  real files   15000 pairs   false positives    0   (0.000%)
  broken synthetic      179 pairs   missed             0   (100.000%)
  broken real files    8282 pairs   missed             0   (100.000%)

  by failure mode                   detected
    ascii-expansion                 11/11    100%
    strip-diacritics                17/17    100%
    to-U+FFFD                       29/29    100%
    delete-nonascii                 29/29    100%
    nul-inject                      10/10    100%
    cp932-roundtrip                 22/22    100%
    cp932-swallow                     3/3    100%
    latin1-mojibake                 29/29    100%
    truncate-nonascii-tail          29/29    100%

SCAN MODE (advisory, no intended version available)
  clean  real files    1500 files   false positives    8   (0.533%)
  broken real files    3302 files   missed             0   (100.000%)
```

The two modes are held to different bars, on purpose. **Compare mode blocks a
write**, so a false positive stops real work: the bar is zero, no budget. **Scan
mode has nothing to compare against** and reads a file cold, so it fires on
files that legitimately *contain* mojibake as data — charset mapping tables
(`sbcs-data.js`, `cp949.json`) and the encoding fixtures in the Python standard
library (`test_email.py`, `test_doctest2.txt`). Those are true sightings of the
byte pattern and a wrong verdict about the file, and no rule separates them
without knowing what the file is for. Measured 0.533% here, 0.962% on a Windows
machine with more bundled JavaScript. Advisory, budgeted at 5%.

A clean pair is an edit a real agent performs: rewriting the same text,
appending, indenting, CRLF, reordering lines, adding more non-ASCII, writing
`ae` deliberately, and NFC↔NFD normalisation. Those 15,000 clean pairs are what
the 0 false positives are counted against; the corpus covers 30 synthetic
samples across 18 languages and 5 symbol sets, plus the real files above.

Ground truth for the corrupted side is decided without asking the detector: a
transform only counts as corruption if the result is **not** Unicode-equivalent
to the original. `NFD("한")` is a different codepoint sequence but the same text,
so staying quiet about it is correct behaviour, not a miss.

## Install

Zero dependencies, one file, Python 3.8+.

```bash
curl -O https://raw.githubusercontent.com/yoggydev/mojibake-guard/main/mojibake_guard.py
```

Add to `.claude/settings.json` (project) or `~/.claude/settings.json` (global):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [
          { "type": "command", "command": "python3 /abs/path/to/mojibake_guard.py" }
        ]
      }
    ]
  }
}
```

Exit 0 = silent. Exit 2 = the write is flagged and the reason goes to the agent,
which then rewrites the file itself.

## The CP932 sweep

`cp932_sweep.py` answers one question completely rather than approximately:
when a Japanese character is followed by a printable ASCII character and the
pair goes through a broken encoding path, does the ASCII character survive?

The space is small enough to execute in full, so it is executed in full — every
character CP932 stores in two bytes, every printable ASCII character, every
path. Nothing sampled, nothing extrapolated.

```
characters (CP932 double-byte)   9,206     enumerated, not sampled
ascii printable                     95     0x20-0x7E, all of them
paths                                6     concrete encode/decode pairs
combinations (char x ascii)    874,570
cells executed               5,247,420     in 14.8s
```

| path | ASCII lost | distinct ASCII affected |
|---|---:|---:|
| `cp932_bytes_read_as_utf8_ignore` | 0 / 874,570 | 0 / 95 |
| `cp932_bytes_read_as_utf8_replace` | 0 / 874,570 | 0 / 95 |
| `utf8_bytes_read_as_cp932_ignore` | 248,709 (28.44%) | **63 / 95** |
| `cp932_via_latin1_to_utf8` | 0 / 874,570 | 0 / 95 |
| `cp932_trail_byte_lost` | 566,265 (64.75%) | **63 / 95** |
| `cp932_lead_byte_lost` | 157,710 (18.03%) | **63 / 95** |

The 63 are identical in all three failing paths, and they are exactly
`0x40`–`0x7E`:

```
lost   @ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~   63
safe    !"#$%&'()*+,-./0123456789:;<=>?                                  32
```

Which is why a vanished `{` or `}` surfaces later as an unmatched brace on an
unrelated line, while a quote or a digit never does. `CP932_FRAGILE` in the
detector is drawn from this boundary.

### The result is bit-identical on every machine

The sweep was run twice — Linux 6.18 / Python 3.11, and Windows 11 / Python
3.12. Both produced a 51,683,609-byte CSV of 972,684 rows with the same digest:

```
e2aec553831feeaee9abd2ebef2212d3ae13a084d8e7dc2050cd504330a39134
```

So the 50 MB of rows is not committed to this repository, and it does not need
to be. Run `python cp932_sweep.py` yourself and hash the result: if it matches
the line above, you have reproduced the whole thing, and if it does not, one of
us has something to explain. That is a stronger claim than a table of numbers
you would have to take on trust, and it is not a claim about privileged data —
the sweep is deterministic, so the file belongs to anyone who runs it.

## Reproducing the bug it was built for

`repro.py` is a separate, deliberately small harness. The corruption people
report is intermittent, so a single run proves nothing and a maintainer on a
machine where it does not happen reasonably says "cannot reproduce". It
measures a **rate** instead:

1. make an empty temp directory — no `CLAUDE.md`, no project settings
2. ask Claude Code, headless, to write one known string into a file
3. compare the file to that string, codepoint by codepoint

```bash
python3 repro.py --runs 5 --mode plain      # "put this text in a file"
python3 repro.py --runs 5 --mode compose    # the model writes the words itself
python3 repro.py --render                   # rebuild the table
```

Results append to `results/matrix.jsonl` and render to `results/MATRIX.md`,
one row per (date, version, model, OS, mode). Rows are never edited.

**Current state: 89 runs, 0 reproductions** — Claude Code 2.1.263 on Linux with
haiku-4.5 and sonnet, and 2.1.239 on Windows 11 with opus. That is a negative
result, not a clean bill of health: the reports that started this name v2.0.70
and macOS, and neither is covered here. If you run it somewhere the bug does
happen, the table is the thing worth sharing.

Two bugs in the harness were found and fixed before it was published, both of
which would have produced fake positives:

- **Inflection read as corruption.** In `compose` mode Czech turned the
  required word `kůň` into the correct `koně`. `ů` and `ň` genuinely vanished,
  so the check fired — but that is grammar, not corruption. The prompt now
  requires the words unchanged in form.
- **The harness corrupting its own input.** `subprocess` with `text=True`
  encodes stdin with the locale codec. On a Japanese Windows console that is
  CP932, so the prompt would have been mangled before Claude Code ever saw it
  and every run would have "failed". Stdin and stdout are now pinned to UTF-8.

A checker that has never caught itself being wrong has not been checked.

## Other modes

```bash
# check one file against a known-good copy
python3 mojibake_guard.py --intended good.md --actual written.md

# find already-corrupted files, no reference needed
python3 mojibake_guard.py --scan $(git ls-files '*.md' '*.py')

# reproduce the numbers above
python3 mojibake_guard.py --selftest
```

## What it does not catch

Stated plainly, because a checker you cannot trust the limits of is worse than
none.

- **Loss of ASCII-only content.** If a write drops a paragraph of pure English,
  nothing here fires. Catching that would mean flagging every partial edit.
- **Position.** The comparison is a multiset, so it is blind to order. Two
  non-ASCII characters swapped with each other (`Möhre Bäcker` → `Mähre Böcker`,
  verified: no finding) leave the counts identical and nothing fires. Loss is
  caught; rearrangement is not.
- **`MultiEdit` where a later edit overwrites an earlier one's non-ASCII text.**
  The intended text is the concatenation of every `new_string`, so a character
  that was legitimately replaced within the same call can be reported as lost.
  Rare, and it fails loud rather than silent.
- **Corruption that happens after the hook runs** — a formatter, a git filter, a
  later tool. This checks the moment of the write.
- It detects. It does not repair. Repair is the agent's job, which is the point:
  the agent is the one that can rewrite the file correctly.

## Prior art

`--scan` overlaps with existing mojibake finders such as `ftfy`. The part that
is new is the comparison against the *intended* text at write time, which is
what makes a silent drop detectable at all: once the file is on disk alone, a
dropped `ö` is indistinguishable from an `o` someone meant to type.

## Licence

MIT.
