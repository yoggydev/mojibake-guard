#!/usr/bin/env python3
"""
repro.py — a deterministic reproduction harness for the non-ASCII corruption
reported in anthropics/claude-code (#14131, #13939, #7335, #14437, #17324 …).

Why a harness and not a bug report: the corruption is intermittent. One run
proves nothing, and a maintainer on a machine where it does not happen sees
"cannot reproduce". So this measures a RATE, not a yes/no.

What one run does
  1. make an empty temp directory (no CLAUDE.md, no project settings)
  2. ask Claude Code, headless, to write one known string verbatim into a file
  3. read the file back and compare it to that string, codepoint by codepoint

Any character that was in the prompt and is not in the file is corruption.
No dictionary, no language knowledge, no judgement call.

Usage
  python3 repro.py --runs 5                       # all languages
  python3 repro.py --runs 10 --langs de tr fr
  python3 repro.py --runs 5 --model opus
  python3 repro.py --render                       # rebuild MATRIX.md only

Results append to results/matrix.jsonl and render to results/MATRIX.md.
Nothing is ever overwritten: the ledger is the point.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter, OrderedDict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mojibake_guard import SEV_BLOCK, analyse  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")
LEDGER = os.path.join(RESULTS_DIR, "matrix.jsonl")
MATRIX_MD = os.path.join(RESULTS_DIR, "MATRIX.md")

OUT_NAME = "out.txt"

# One short, fixed string per language. Kept small so a run is cheap, and
# chosen so every string carries several of the characters the issues name.
SAMPLES = OrderedDict([
    ("de", "Die Verzögerung führt zu größeren Ausfällen. Straße, Übung, schön."),
    ("tr", "Türkçe karakterler: ışğüşöç. İstanbul'da yağmur yağıyor."),
    ("es", "La configuración añade una señal. ¿Cuántos años? ¡Sí!"),
    ("fr", "Créer une requête déjà prête. L'élève a réussi l'épreuve à Noël."),
    ("nb", "Åse og Øyvind spiste smørbrød i Ålesund. Räksmörgås."),
    ("pl", "Zażółć gęślą jaźń. Ćma, źdźbło, ręka, wąż."),
    ("cs", "Příliš žluťoučký kůň úpěl ďábelské ódy."),
    ("ja", "文字化けが起きるとファイルが黙って壊れる。設定{ここ}を読む。"),
    ("ru", "Проверка кодировки. Файл тихо ломается при записи."),
    ("zh", "这是一个中文测试文件。编码错误会导致数据丢失。"),
])

# Words the model must produce itself in "compose" mode. Given in the prompt,
# so checking for them needs no dictionary: they are the intended text.
COMPOSE_WORDS = {
    "de": ["Verzögerung", "Straße", "für", "können", "Größe"],
    "tr": ["gecikme", "yağmur", "İstanbul", "şehir", "çalışıyor"],
    "es": ["configuración", "años", "señal", "está", "número"],
    "fr": ["requête", "élève", "déjà", "problème", "être"],
    "nb": ["forsinkelse", "smørbrød", "Ålesund", "større", "påvirker"],
    "pl": ["opóźnienie", "gęślą", "wąż", "źródło", "ćwiczenie"],
    "cs": ["zpoždění", "kůň", "ďábelské", "příliš", "žluťoučký"],
    "ja": ["文字化け", "設定", "壊れる", "確認", "実行"],
    "ru": ["задержка", "кодировка", "проверка", "файл", "запись"],
    "zh": ["编码", "错误", "文件", "测试", "数据"],
}

# Three ways to ask. The bug is reported in ordinary use, so a clean result
# under a strong "do not alter" instruction proves much less than a clean
# result under the plain one — and the compose mode is closest of all to what
# #14131 describes, because the model writes the words itself.
PROMPTS = {
    "strict": (
        "Write the following text verbatim into ./{out} using the Write tool.\n"
        "Do not translate it, do not transliterate it, do not normalise it, do\n"
        "not fix it, do not change a single character. Copy it exactly.\n"
        "When the file is written, reply with just: DONE\n"
        "\n"
        "TEXT:\n"
        "{text}\n"
    ),
    "plain": (
        "Put this text in ./{out}.\n"
        "\n"
        "{text}\n"
    ),
    # The "exactly as written" clause is not decoration. Without it, an
    # inflected language declines the given word and the check misfires:
    # Czech turned the required "kůň" into the correct "koně", which looks
    # identical to a dropped diacritic. Measured, then fixed.
    "compose": (
        "Write a short paragraph (3 sentences) into ./{out} about scheduling a\n"
        "software release. Write it in the same language as the words below.\n"
        "Every one of these words must appear in it exactly as written here,\n"
        "in this exact form - do not inflect, decline, conjugate or otherwise\n"
        "change them:\n"
        "\n"
        "{text}\n"
    ),
}

# Every field of the invocation, recorded so a reader can repeat it exactly.
CLAUDE_ARGS = [
    "-p",
    "--output-format", "json",
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Write",
]


# On Windows the launcher is claude.cmd, which subprocess will not find by the
# bare name. Resolve it once, here, so every call uses a real path.
CLAUDE_BIN = shutil.which("claude") or "claude"


def claude_version() -> str:
    try:
        out = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             timeout=60).stdout.strip()
        return out.split()[0] if out else "unknown"
    except Exception:
        return "unknown"


def intended_for(lang: str, mode: str) -> str:
    """What the file must contain, whichever way we asked."""
    if mode == "compose":
        return " ".join(COMPOSE_WORDS[lang])
    return SAMPLES[lang]


def one_run(lang: str, mode: str, model: str, timeout: int) -> dict:
    """One isolated attempt. Returns a record; never raises."""
    workdir = tempfile.mkdtemp(prefix="mgrepro-")
    rec = {"lang": lang, "mode": mode, "model_requested": model}
    text = intended_for(lang, mode)
    try:
        prompt = PROMPTS[mode].format(out=OUT_NAME, text=text)
        argv = [CLAUDE_BIN] + CLAUDE_ARGS
        if model:
            argv += ["--model", model]
        t0 = time.time()
        proc = subprocess.run(argv, input=prompt, capture_output=True,
                              encoding="utf-8", errors="replace",
                              cwd=workdir, timeout=timeout)
        rec["seconds"] = round(time.time() - t0, 1)

        meta = {}
        try:
            meta = json.loads(proc.stdout or "{}")
        except ValueError:
            pass
        # modelUsage lists every model the session touched, including the
        # small one Claude Code uses for side tasks. The model under test is
        # the one that did the work, so pick by output tokens - not by order.
        usage = (meta.get("modelUsage") or {})
        rec["model_resolved"] = (
            max(usage.items(), key=lambda kv: kv[1].get("outputTokens", 0))[0]
            if usage else "unknown")
        rec["models_seen"] = sorted(usage)
        rec["cost_usd"] = meta.get("total_cost_usd")
        rec["session_id"] = meta.get("session_id")

        path = os.path.join(workdir, OUT_NAME)
        if not os.path.isfile(path):
            rec["verdict"] = "NOFILE"
            rec["codes"] = []
            rec["stderr_tail"] = (proc.stderr or "")[-300:]
            return rec

        with open(path, "rb") as f:
            actual = f.read().decode("utf-8", errors="replace")

        findings = [f for f in analyse(text, actual) if f.severity == SEV_BLOCK]
        rec["codes"] = [f.code for f in findings]
        rec["verdict"] = "CORRUPT" if findings else "OK"
        if findings:
            rec["detail"] = findings[0].detail[:400]
            rec["actual"] = actual[:600]
        return rec
    except subprocess.TimeoutExpired:
        rec["verdict"] = "TIMEOUT"
        rec["codes"] = []
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["verdict"] = "ERROR"
        rec["codes"] = []
        rec["error"] = repr(exc)[:300]
        return rec
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_matrix(langs, runs, model, timeout, mode="plain", verbose=True) -> dict:
    started = datetime.now(timezone.utc)
    version = claude_version()
    per_lang = OrderedDict()
    all_records = []

    for lang in langs:
        results = []
        for i in range(runs):
            rec = one_run(lang, mode, model, timeout)
            results.append(rec)
            all_records.append(rec)
            if verbose:
                mark = {"OK": ".", "CORRUPT": "X", "NOFILE": "?",
                        "TIMEOUT": "T", "ERROR": "E"}.get(rec["verdict"], "?")
                sys.stdout.write("%s%s " % (lang if i == 0 else "", mark))
                sys.stdout.flush()
        counts = Counter(r["verdict"] for r in results)
        codes = Counter(c for r in results for c in r.get("codes", []))
        per_lang[lang] = {
            "runs": runs,
            "corrupt": counts.get("CORRUPT", 0),
            "ok": counts.get("OK", 0),
            "nofile": counts.get("NOFILE", 0),
            "timeout": counts.get("TIMEOUT", 0),
            "error": counts.get("ERROR", 0),
            "codes": dict(codes),
            "failures": [
                {k: v for k, v in r.items()
                 if k in ("verdict", "codes", "detail", "actual", "seconds")}
                for r in results if r["verdict"] != "OK"
            ],
        }
        if verbose:
            print(" -> %d/%d corrupt" % (per_lang[lang]["corrupt"], runs))

    resolved = Counter(r.get("model_resolved") for r in all_records
                       if r.get("model_resolved"))
    cost = sum(r.get("cost_usd") or 0 for r in all_records)

    return {
        "utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claude_code": version,
        "model_requested": model or "(default)",
        "model_resolved": resolved.most_common(1)[0][0] if resolved else "unknown",
        "os": "%s %s" % (platform.system(), platform.release()),
        "python": platform.python_version(),
        "runs_per_lang": runs,
        "mode": mode,
        "prompt": PROMPTS[mode].format(out=OUT_NAME, text="<SAMPLE>"),
        "claude_args": CLAUDE_ARGS,
        "languages": per_lang,
        "total_cost_usd": round(cost, 4),
    }


def append_ledger(row: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_matrix() -> str:
    if not os.path.isfile(LEDGER):
        return "no results yet\n"
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    langs = []
    for r in rows:
        for lang in r["languages"]:
            if lang not in langs:
                langs.append(lang)

    out = [
        "# Corruption rate by Claude Code version and language",
        "",
        "Each cell is `corrupted / runs`. One run = one headless Claude Code",
        "session asked to write a fixed string verbatim into an empty directory,",
        "then the file compared to that string codepoint by codepoint.",
        "Append-only: a row is never edited after it is written.",
        "",
        "| date (UTC) | Claude Code | model | OS | mode | N | " + " | ".join(langs) + " |",
        "|---|---|---|---|---|---|" + "---|" * len(langs),
    ]
    for r in rows:
        cells = []
        for lang in langs:
            d = r["languages"].get(lang)
            if not d:
                cells.append("–")
                continue
            bad = d["corrupt"] + d["nofile"] + d["timeout"] + d["error"]
            cells.append(("**%d/%d**" if d["corrupt"] else "%d/%d")
                         % (bad, d["runs"]))
        out.append("| %s | %s | %s | %s | %s | %d | %s |" % (
            r["utc"][:10], r["claude_code"],
            r.get("model_requested") or r["model_resolved"], r["os"],
            r.get("mode", "strict"), r["runs_per_lang"], " | ".join(cells)))

    out += ["", "## Findings seen", ""]
    seen = Counter()
    for r in rows:
        for d in r["languages"].values():
            seen.update(d.get("codes", {}))
    if seen:
        for code, n in seen.most_common():
            out.append("- `%s` x%d" % (code, n))
    else:
        out.append("- none yet: every run so far preserved every character")

    out += ["", "## How to reproduce", "",
            "```bash",
            "python3 repro.py --runs 5 --mode plain",
            "python3 repro.py --runs 5 --mode compose",
            "```", ""]

    os.makedirs(RESULTS_DIR, exist_ok=True)
    text = "\n".join(out) + "\n"
    with open(MATRIX_MD, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(prog="repro.py")
    ap.add_argument("--runs", type=int, default=5, help="runs per language")
    ap.add_argument("--langs", nargs="+", default=list(SAMPLES),
                    choices=list(SAMPLES))
    ap.add_argument("--model", default="", help="passed to claude --model")
    ap.add_argument("--mode", default="plain", choices=list(PROMPTS),
                    help="strict = told not to alter; plain = no instruction; "
                         "compose = the model writes the words itself")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--render", action="store_true",
                    help="only rebuild MATRIX.md from the ledger")
    args = ap.parse_args()

    if args.render:
        print(render_matrix())
        return 0

    if not shutil.which("claude"):
        print("claude CLI not found on PATH", file=sys.stderr)
        return 2
    # Prove the harness is not the thing doing the corrupting.
    probe = "ä ö ü ß ı ş å ø é ñ 文字化け Проверка"
    if any(ord(c) > 127 for c in probe) and probe.encode("utf-8").decode("utf-8") != probe:
        print("self-check failed: this Python cannot round-trip UTF-8",
              file=sys.stderr)
        return 2

    row = run_matrix(args.langs, args.runs, args.model, args.timeout, args.mode)
    append_ledger(row)
    render_matrix()
    print()
    print(json.dumps({k: v for k, v in row.items()
                      if k not in ("prompt", "claude_args")},
                     ensure_ascii=False, indent=2))
    print("\nledger: %s\nmatrix: %s" % (LEDGER, MATRIX_MD))
    return 0


if __name__ == "__main__":
    sys.exit(main())
