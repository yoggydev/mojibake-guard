# Data commitment — CP932 x printable-ASCII exhaustive sweep (874,570 combinations, 5,247,420 cells)

These files exist on my machine as of the date below. Their contents are
not published here. The digests are, so that anyone who later receives a
file can hash it and confirm it is the same one, unchanged.

| field | value |
|---|---|
| committed (UTC) | 2026-09-06 08:46:41Z |
| files | 1 |
| total bytes | 51,683,609 |
| combined SHA-256 | `fa7047b2e37041f258d407c45bc6458ce4350db2924f16329e5060ace2266424` |
| platform | Windows 11 |

## Files

| file | bytes | rows | SHA-256 |
|---|---:|---:|---|
| `ascii-lost.csv` | 51,683,609 | 972685 | `e2aec553831feeaee9abd2ebef2212d3ae13a084d8e7dc2050cd504330a39134` |

## Verify a file you were given

```powershell
Get-FileHash .\<file> -Algorithm SHA256
```

```bash
sha256sum <file>      # Linux
shasum -a 256 <file>  # macOS
```

The combined digest is the SHA-256 of the lines `<file digest> <file name>`,
one per file, in the order listed above.
