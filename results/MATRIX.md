# Corruption rate by Claude Code version and language

Each cell is `corrupted / runs`. For every mode but `session`, one run
is one headless Claude Code request in an empty directory, with what
came back compared to the intended text codepoint by codepoint.
A `sessionxN` row is different: one run is N small Edit turns inside
ONE session, each turn checked on its own, and the cell counts a run as
corrupt if any of its turns was.
Append-only: a row is never edited after it is written.

| date (UTC) | Claude Code | model | OS | mode | N | de | tr | es | fr | nb | pl | cs | ja | ru | zh |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-06 | 2.1.263 | haiku | Linux 6.18.44-fc-v24 | plain | 5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| 2026-09-06 | 2.1.263 | sonnet | Linux 6.18.44-fc-v24 | compose | 5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | – | 0/5 | – | – | – |
| 2026-09-06 | 2.1.239 | opus | Windows 11 | compose | 3 | 0/3 | 0/3 | – | 0/3 | – | – | – | – | – | – |
| 2026-09-06 | 2.1.263 | opus | Linux 6.18.44-fc-v24 | long | 3 | 0/3 | 0/3 | – | 0/3 | – | – | – | – | – | – |
| 2026-09-06 | 2.1.263 | opus | Linux 6.18.44-fc-v24 | chat | 1 | 0/1 | – | – | – | – | – | – | – | – | – |
| 2026-09-06 | 2.1.263 | opus | Linux 6.18.44-fc-v24 | chat | 5 | 0/5 | 0/5 | – | 0/5 | – | – | – | – | – | – |
| 2026-09-06 | 2.1.263 | opus | Linux 6.18.44-fc-v24 | chat+lang | 1 | 0/1 | – | – | – | – | – | – | – | – | – |
| 2026-09-06 | 2.1.263 | opus | Linux 6.18.44-fc-v24 | chat+lang | 5 | 0/5 | 0/5 | – | 0/5 | – | – | – | – | – | – |
| 2026-09-13 | 2.1.239 | opus | Windows 11 | sessionx6 | 3 | 0/3 | – | – | – | – | – | – | – | – | – |
| 2026-09-13 | 2.1.270 | opus | Linux 6.18.44-fc-v24 | sessionx6+prose | 3 | 0/3 | – | – | – | – | – | – | – | – | – |
| 2026-09-13 | 2.1.270 | opus | Linux 6.18.44-fc-v24 | sessionx6 | 3 | 0/3 | – | – | – | – | – | – | – | – | – |
| 2026-09-13 | 2.1.270 | opus | Linux 6.18.44-fc-v24 | natural | 3 | 0/3 | – | – | – | – | – | – | – | – | – |

## Findings seen

- none yet: every run so far preserved every character

## How to reproduce

```bash
python3 repro.py --runs 5 --mode plain
python3 repro.py --runs 5 --mode compose
python3 repro.py --runs 3 --mode session --turns 6 --langs de
```


## Session mode: corruption by turn depth

`n/m` = corrupt / turns that actually landed an edit. A turn
that never landed is counted in neither.

`seed` is what the file contained before the session started.
`ascii` means nothing on disk could be corrupted, so the
column measures generation only. `prose` means the file
already held the language, and `seed lost` counts runs where
an edit destroyed text that was there from the start — a
file-handling failure, which the `ascii` seed cannot see.

| date | Claude Code | OS | lang | seed | t1 | t2 | t3 | t4 | t5 | t6 | skipped | cross-turn | seed lost |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-09-13 | 2.1.239 | Windows 11 | de | ascii | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0 | 0 | – |
| 2026-09-13 | 2.1.270 | Linux 6.18.44-fc-v24 | de | prose | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0 | 0 | 0 |
| 2026-09-13 | 2.1.270 | Linux 6.18.44-fc-v24 | de | ascii | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0/3 | 0 | 0 | – |
