# Corruption rate by Claude Code version and language

Each cell is `corrupted / runs`. One run = one headless Claude Code
session asked to write a fixed string verbatim into an empty directory,
then the file compared to that string codepoint by codepoint.
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

## Findings seen

- none yet: every run so far preserved every character

## How to reproduce

```bash
python3 repro.py --runs 5 --mode plain
python3 repro.py --runs 5 --mode compose
```

