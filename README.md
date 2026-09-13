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
python3 repro.py --mode plain   --runs 5    # "put this text in a file"
python3 repro.py --mode compose --runs 5    # the model writes the words itself
python3 repro.py --mode long    --runs 3    # 700+ words, 20 required words
python3 repro.py --mode chat    --runs 5    # no file at all: the reply text
python3 repro.py --mode chat    --runs 5 --language   # same, `language` set
python3 repro.py --mode session --runs 3 --turns 6    # many small Edits, ONE session
python3 repro.py --mode natural --runs 3 --langs de   # NO instruction about characters
python3 repro.py --render                   # rebuild the table
```

The modes are not interchangeable, and picking the wrong one is how this
harness spent its first hundred runs testing the wrong thing. The author of
[#14131](https://github.com/anthropics/claude-code/issues/14131) says in the
thread that his issue is *"ASCII substitution in chat responses ... different
context (chat output vs file editing), likely different root cause"*. So:

| mode | what is checked | matches |
|---|---|---|
| `plain` `compose` `long` | the file the Write tool produced | #13939, #7335 |
| `chat` | the reply text, no file involved | #14131 |
| `session` | several small Edits inside one live session, per turn | #14131 as its author describes it in practice |
| `natural` | a German document summarised with no instruction about characters at all | #14131, with the suppression removed |

### `natural` mode, and the instruction that may have been hiding the bug

Every mode above hands the model a word list and says *"use these exactly as
written, do not inflect, decline, conjugate or otherwise change them"*. That
clause is in the false-positive list below: it exists because Czech declined a
required word and the codepoint check read the grammar as corruption. It made
the oracle sound.

It is also, if #14131 is the model transliterating German of its own accord,
the single most reliable way to *suppress the thing being measured*. A hundred
clean runs taken under that instruction mean much less than they appear to.

`natural` removes it. A German document is placed on disk, the model is asked
to summarise it, and **nothing is said about characters**. The intended
spelling is then known from the file rather than from the prompt.

The price is that the check is no longer a codepoint comparison. It looks for a
seed word's ASCII-folded form in the newly written text, which is a
**heuristic**, and its false-positive class is obvious: a fold that is itself a
real German word. `schön` folds to `schon` ("already"), `Größe` to `Grosse`
(the ordinary Swiss spelling), `Ausfällen` to `Ausfallen`. Those forms are
excluded per form rather than per word, so `für` is still watched through
`fuer` while `fur` is ignored. **A hit from this mode is a `LEAD`, not a
reproduction** — the verdict is named that way so it cannot be counted as one
by accident. Read the sentence.

A clean result is only meaningful if the model actually reused the vocabulary,
so every run records how many of the watched words came back: the first runs
reused 17 of 19 and kept every umlaut.

### `session` mode, and why it exists

On 2026-09-13 the author of #14131 ran this harness on macOS — the platform
row that was missing above — on Claude Code 2.1.270 with Opus 5. `chat`,
`chat --language` and `long` all came back 0/5. He then explained why that is
not the good news it looks like:

> In my real daily use, this doesn't need a long conversation or compaction to
> show up — I regularly see it after just 3-5 turns, sometimes almost
> immediately. And I see it primarily in file writes (the code/diff preview
> shown inline in the CLI), not in the chat reply text itself. This harness's
> chat mode checks the reply text, and its file-writing modes are still a
> single isolated request — neither matches what I actually hit: many small
> turns building up file edits in one working session.

Every mode above this one is a single isolated request. That is the variable
`session` changes, and nothing else: the language, the required words and the
codepoint check are the same.

One run seeds a temp directory with a **pure-ASCII** file —

```
# Release notes

- TURN-01: TODO
- TURN-02: TODO
...
```

— then makes `--turns` requests **in one session**, chaining `--resume`, each
asking for a single small `Edit` to one line. `Write` is not in `--allowedTools`,
so a model that cannot use `Edit` fails visibly instead of quietly rewriting the
whole file down a different code path. Nothing non-ASCII is in the seed, so
every non-ASCII character checked later was produced by the model during the
session.

Each turn is checked on its own, which buys two things a single request cannot
measure:

- **the turn depth at which corruption first appears** — reported per turn
  index, so "clean at turn 1, broken at turn 4" is visible as such rather than
  averaged into one rate
- **cross-turn damage** — every line passes its own check, but the file as a
  whole has lost characters an earlier turn already wrote correctly. That is
  collateral damage from re-editing, not a generation failure, and it has a
  separate `CROSS_TURN_*` code

`--language` sets Claude Code's `language` setting for the run. A Claude Code
collaborator asked in #14131 on 2026-05-31 whether specifying it changes the
rate; three months later the thread has two opinions and no measurements in
reply. This flag is that A/B: same prompt, same model, setting present or absent.

Results append to `results/matrix.jsonl` and render to `results/MATRIX.md`,
one row per (date, version, model, OS, mode). Rows are never edited.

**Current state: 130 runs, 0 reproductions.**

| what was checked | model | Claude Code | OS | runs | lost |
|---|---|---|---:|---:|---:|
| reply text | opus | 2.1.263 | Linux | 16 | 0 |
| reply text, `language` set | opus | 2.1.263 | Linux | 16 | 0 |
| Write, 700+ word document | opus | 2.1.263 | Linux | 9 | 0 |
| Write, short text | sonnet | 2.1.263 | Linux | 30 | 0 |
| Write, short text | haiku | 2.1.263 | Linux | 50 | 0 |
| Write, short text | opus | 2.1.239 | Windows 11 | 9 | 0 |

That is a negative result, not a clean bill of health. The original report is
v2.0.70 and these runs are 2.1.239 / 2.1.263, headless `-p` is not an
interactive session, and none of that is evidence the bug is gone.

The missing macOS row was the obvious suspect, and it has since been ruled out.
On 2026-09-13 the author of #14131 ran `chat`, `chat --language` and `long` on
macOS with 2.1.270 and Opus 5 and got 0/5 on all three — reported in the thread,
not in this ledger, so it is not a row in the table above. Platform was not the
variable. What he said next is what `session` mode below is for: the shape of
the request was wrong, in every mode, for the thing he actually hits.

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

- **The harness crashing on the encoding it measures.** Found on a ja-JP
  Windows console on 2026-09-13, the first time `--render` was run there.
  `render_matrix()` wrote `MATRIX.md` correctly — it opens the file as UTF-8 —
  and then died printing the same text to the console, because `–` (U+2013)
  has no CP932 encoding. The cosmetic half of that is a table. The half that
  mattered is that the final result is printed with `ensure_ascii=False`, and
  `detail` and `actual` carry non-ASCII **only when a run was corrupt**. On the
  one console this project exists to serve, the harness would have crashed at
  exactly the moment it first caught something, and every clean run before that
  would have looked fine. It was never hit because every Windows run so far
  reported zero failures. `stdout` and `stderr` are now pinned to UTF-8 at
  startup, and `test_session_mode.py` pins the regression: a real CP932 stream
  is shown to reject U+2013, and a `CORRUPT`-shaped record is shown to survive
  once the stream is reconfigured.

`session` mode adds a third failure class. This one was designed against rather
than discovered afterwards, which is a weaker claim and worth saying plainly:

- **A turn that never landed is not corruption.** If a turn answers without
  calling `Edit`, or edits the wrong line, the required words are simply absent
  from the file — and a codepoint comparison cannot tell "the model dropped the
  umlaut" from "the model never wrote the word". Counting the second as a
  reproduction would manufacture exactly the result the harness is looking for.
  Each turn therefore writes an ASCII marker (`TURN-04`) that cannot itself be
  corrupted; if the marker line is missing or still reads `TODO`, that turn is
  `SKIPPED` and is excluded from both the numerator and the denominator.

`test_session_mode.py` pins all of this with a scripted fake in place of
`claude` — no API calls, no cost. It asserts that a clean session reads clean,
that an ASCII substitution at turn 4 is caught *at turn 4*, that a skipped turn
invents no corruption, that cross-turn damage is caught when every individual
line passes, and that a session where nothing lands is `NOFILE` rather than a
reproduction.

```bash
python3 test_session_mode.py
```

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
