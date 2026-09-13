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

Modes differ in HOW the text is asked for, because that turns out to matter
more than the platform. `session` is the only one that is not a single
isolated request: it makes several small Edit-tool changes inside one live
session and checks each turn on its own, which is the shape the author of
#14131 says he actually hits the bug in.

Usage
  python3 repro.py --runs 5                       # all languages
  python3 repro.py --runs 10 --langs de tr fr
  python3 repro.py --runs 5 --model opus
  python3 repro.py --runs 3 --langs de --mode session --turns 6
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

# The reports describe long sessions and say the substitution happens
# "inconsistently within the same response". A 3-sentence paragraph gives the
# failure very few chances to appear. This list is the same idea as
# COMPOSE_WORDS but long enough to force a document, so a rate measured with it
# is comparable to what people actually hit.
LONG_WORDS = {
    "de": ["Verzögerung", "Straße", "für", "können", "Größe", "Änderung",
           "Prüfung", "Ausführung", "Schlüssel", "Übersicht", "Qualität",
           "Rückgabe", "Behörde", "Gebäude", "Vergütung", "Erhöhung",
           "Wörterbuch", "Bestätigung", "Zurücksetzen", "Überprüfung"],
    "tr": ["gecikme", "yağmur", "İstanbul", "şehir", "çalışıyor", "değişiklik",
           "güvenlik", "başlangıç", "öğrenci", "sınıf", "açıklama", "günlük",
           "yönetim", "işlem", "sürüm", "çözüm", "bağlantı", "geliştirme",
           "kullanıcı", "doğrulama"],
    "fr": ["requête", "élève", "déjà", "problème", "être", "réponse",
           "détaillé", "événement", "opération", "précédent", "référence",
           "génération", "création", "réussite", "sécurité", "propriété",
           "vérité", "unité", "qualité", "entité"],
    "cs": ["zpoždění", "kůň", "ďábelské", "příliš", "žluťoučký", "úpěl",
           "možnost", "přístup", "změna", "výsledek", "chyba", "nastavení",
           "uživatel", "položka", "hodnota", "soubor", "řetězec", "úroveň",
           "šablona", "návrat"],
    "ja": ["文字化け", "設定", "壊れる", "確認", "実行", "検証", "変換",
           "取得", "削除", "更新", "処理", "出力", "入力", "境界", "符号",
           "計測", "再現", "誤検出", "実測", "台帳"],
    "es": ["configuración", "años", "señal", "está", "número", "versión",
           "aplicación", "también", "según", "última", "código", "diseño",
           "revisión", "conexión", "función", "compañía", "pequeño",
           "administración", "información", "días"],
    "nb": ["forsinkelse", "smørbrød", "Ålesund", "større", "påvirker",
           "løsning", "årsak", "gjennomføre", "første", "både", "økning",
           "tilbakemelding", "håndtere", "avhengig", "møte", "bør",
           "utførelse", "sjekkliste", "innstilling", "nødvendig"],
    "pl": ["opóźnienie", "gęślą", "wąż", "źródło", "ćwiczenie", "wdrożenie",
           "użytkownik", "błąd", "zmiana", "ustawienia", "wartość",
           "połączenie", "część", "wyjście", "różnica", "sprawdzić",
           "będzie", "można", "wcześniej", "zapisać"],
    "ru": ["задержка", "кодировка", "проверка", "файл", "запись", "версия",
           "настройка", "значение", "ошибка", "результат", "изменение",
           "выполнение", "пользователь", "подключение", "уровень", "объём",
           "строка", "возврат", "сообщение", "обновление"],
    "zh": ["编码", "错误", "文件", "测试", "数据", "版本", "配置", "监控",
           "回滚", "部署", "验证", "输出", "输入", "连接", "设置", "结果",
           "变更", "记录", "检查", "恢复"],
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
    # The first version of this prompt was written in German, so a Chinese run
    # got a German reply with the required words translated (bian-ma -> "Code")
    # and the check read 40 "lost" characters that were never corrupted. Found
    # by running the published clone before posting the command anywhere.
    #
    # #14131 is NOT about files. Its author says so explicitly in the thread:
    # "This issue: ASCII substitution in chat responses on macOS ... different
    # context (chat output vs file editing), likely different root cause."
    # So this mode writes no file at all and checks the reply itself. The
    # file-writing modes above match #13939 and #7335; this one matches #14131.
    "chat": (
        "Reply in the same language as the words listed below. Do not reply in\n"
        "English or in any other language.\n"
        "Explain briefly, in about 200 words, how to plan and ship a software\n"
        "release. Every one of the words below must appear in your reply at\n"
        "least once, exactly as written here, in this exact form - do not\n"
        "translate them, and do not inflect, decline or conjugate them:\n"
        "\n"
        "{text}\n"
    ),
    # Every mode above is ONE isolated request. The author of #14131 ran this
    # harness on macOS on 2026-09-13 (2.1.270, Opus 5) and got 0/5 on chat,
    # 0/5 on chat+language and 0/5 on long -- then said why:
    #
    #   "In my real daily use, this doesn't need a long conversation or
    #    compaction to show up - I regularly see it after just 3-5 turns.
    #    And I see it primarily in file writes (the code/diff preview shown
    #    inline in the CLI), not in the chat reply text itself. This harness's
    #    chat mode checks the reply text, and its file-writing modes are still
    #    a single isolated request - neither matches what I actually hit:
    #    many small turns building up file edits in one working session."
    #
    # So the axis this mode adds is TURN DEPTH inside one session, with small
    # Edit-tool changes to a file that is already on disk. Not output length,
    # not platform. Each turn is checked on its own, so the result is a rate
    # per turn index and not just a rate per run.
    "session": (
        "Edit ./{out}. Replace the line\n"
        "\n"
        "- {marker}: TODO\n"
        "\n"
        "with a line that starts with \"- {marker}: \" and then one short\n"
        "sentence about scheduling a software release, written in the same\n"
        "language as the words below. Every one of these words must appear in\n"
        "that sentence at least once, exactly as written here, in this exact\n"
        "form - do not translate them, and do not inflect, decline or\n"
        "conjugate them:\n"
        "\n"
        "{text}\n"
        "\n"
        "Use the Edit tool. Change nothing else in the file.\n"
        "When the edit is done, reply with just: DONE\n"
    ),
    # Length is the variable. Everything else matches compose mode, so the two
    # rates are comparable and the only thing that changed is how much text the
    # model had to produce before it was done.
    "long": (
        "Write technical documentation into ./{out} about deploying and\n"
        "operating a web service: setup, configuration, monitoring, rollback,\n"
        "and troubleshooting. Write it in the same language as the words below.\n"
        "At least 700 words, with headings and several paragraphs per section.\n"
        "Every one of these words must appear in it at least once, exactly as\n"
        "written here, in this exact form - do not inflect, decline, conjugate\n"
        "or otherwise change them:\n"
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


# Chat mode needs no tools at all: the reply text is the thing under test.
CHAT_ARGS = ["-p", "--output-format", "json"]

# Session mode edits a file that already exists, so Read and Edit are the tools
# under test. Write is deliberately NOT allowed: if the model cannot use Edit it
# must fail visibly rather than quietly rewrite the whole file, which would be a
# different code path and a different bug.
SESSION_ARGS = [
    "-p",
    "--output-format", "json",
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read,Edit",
]

SESSION_FILE = "notes.md"


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
    if mode in ("long", "chat", "session"):
        return " ".join(LONG_WORDS[lang])
    return SAMPLES[lang]


def ledger_prompt(mode: str) -> str:
    """The prompt, with the sample blanked, as recorded in the ledger."""
    if mode == "session":
        return PROMPTS[mode].format(out=SESSION_FILE, marker="TURN-NN",
                                    text="<SAMPLE>")
    return PROMPTS[mode].format(out=OUT_NAME, text="<SAMPLE>")


# ------------------------------------------------------------ session mode

def session_seed(turns: int) -> str:
    """The file the session starts from. Pure ASCII on purpose: nothing here
    can be corrupted, so every non-ASCII character found later was produced by
    the model during the session."""
    lines = ["# Release notes", ""]
    lines += ["- TURN-%02d: TODO" % k for k in range(1, turns + 1)]
    return "\n".join(lines) + "\n"


def turn_words(lang: str, turns: int, per_turn: int) -> list:
    """Split the required words across turns. Cycles if there are not enough,
    so --turns and --words-per-turn can be set independently."""
    pool = LONG_WORDS[lang]
    out, i = [], 0
    for _ in range(turns):
        out.append([pool[(i + j) % len(pool)] for j in range(per_turn)])
        i += per_turn
    return out


def marker_line(text: str, marker: str):
    """The line this turn was supposed to rewrite, or None."""
    for line in text.splitlines():
        if marker in line:
            return line
    return None


def one_session_run(lang: str, model: str, timeout: int,
                    language_setting: bool, turns: int, per_turn: int) -> dict:
    """One session, `turns` small Edit turns, checked turn by turn.

    Two things are measured that a single-request run cannot show:
      - at which turn depth corruption first appears
      - whether a later turn damages a line an earlier turn already wrote
        (cross-turn damage: the per-turn checks all pass, the whole file does
        not). That is collateral damage from re-editing, not generation.
    """
    workdir = tempfile.mkdtemp(prefix="mgsess-")
    chunks = turn_words(lang, turns, per_turn)
    rec = {"lang": lang, "mode": "session", "model_requested": model,
           "language_setting": language_setting,
           "turns_requested": turns, "words_per_turn": per_turn}
    path = os.path.join(workdir, SESSION_FILE)
    try:
        seed = session_seed(turns)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(seed)
        seed_lines = {l.split(":")[0].strip("- "): l
                      for l in seed.splitlines() if l.startswith("- ")}

        settings_path = None
        if language_setting:
            settings_path = os.path.join(workdir, "mg-settings.json")
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump({"language": LANGUAGE_NAMES[lang]}, f)

        session_id = None
        session_ids, turn_recs = [], []
        out_tokens, cost, models_seen = Counter(), 0.0, set()
        t0 = time.time()

        for k in range(1, turns + 1):
            marker = "TURN-%02d" % k
            words = chunks[k - 1]
            prompt = PROMPTS["session"].format(
                out=SESSION_FILE, marker=marker, text=" ".join(words))

            argv = [CLAUDE_BIN] + SESSION_ARGS
            # Chain the NEWEST id, not the first: some versions mint a fresh id
            # on --resume. Resuming the original every turn would branch from
            # turn 1 each time and the depth being measured would never build.
            if session_id:
                argv += ["--resume", session_id]
            if settings_path:
                argv += ["--settings", settings_path]
            if model:
                argv += ["--model", model]

            tr = {"turn": k, "words": words}
            try:
                proc = subprocess.run(argv, input=prompt, capture_output=True,
                                      encoding="utf-8", errors="replace",
                                      cwd=workdir, timeout=timeout)
            except subprocess.TimeoutExpired:
                tr.update(verdict="TIMEOUT", codes=[])
                turn_recs.append(tr)
                break

            meta = {}
            try:
                meta = json.loads(proc.stdout or "{}")
            except ValueError:
                pass
            new_id = meta.get("session_id")
            if new_id:
                session_id = new_id
                session_ids.append(new_id)
            for m, u in (meta.get("modelUsage") or {}).items():
                models_seen.add(m)
                out_tokens[m] += u.get("outputTokens", 0)
            cost += meta.get("total_cost_usd") or 0.0

            with open(path, "rb") as f:
                now = f.read().decode("utf-8", errors="replace")
            line = marker_line(now, marker)
            # A turn that never landed is NOT corruption. Without this the
            # missing words would read as dropped characters and every failed
            # Edit would be counted as a reproduction.
            if line is None or line == seed_lines.get(marker):
                tr.update(verdict="SKIPPED", codes=[],
                          stderr_tail=(proc.stderr or "")[-200:])
                turn_recs.append(tr)
                continue

            findings = [f for f in analyse(" ".join(words), line)
                        if f.severity == SEV_BLOCK]
            tr.update(verdict="CORRUPT" if findings else "OK",
                      codes=[f.code for f in findings])
            if findings:
                tr["detail"] = findings[0].detail[:300]
                tr["line"] = line[:300]
            turn_recs.append(tr)

        rec["seconds"] = round(time.time() - t0, 1)
        rec["turns"] = turn_recs
        rec["session_ids"] = session_ids
        rec["one_thread"] = len(set(session_ids)) <= 1
        rec["cost_usd"] = round(cost, 4)
        rec["models_seen"] = sorted(models_seen)
        rec["model_resolved"] = (out_tokens.most_common(1)[0][0]
                                 if out_tokens else "unknown")

        landed = [t for t in turn_recs if t["verdict"] in ("OK", "CORRUPT")]
        rec["turns_landed"] = len(landed)
        codes = {c for t in turn_recs for c in t.get("codes", [])}

        if not landed:
            rec["verdict"] = "NOFILE"
            rec["codes"] = []
            return rec

        # Cross-turn damage: each line passed on its own, but the file as a
        # whole lost characters a landed turn had put there.
        with open(path, "rb") as f:
            whole = f.read().decode("utf-8", errors="replace")
        landed_words = " ".join(w for t in landed for w in t["words"])
        cross = [f for f in analyse(landed_words, whole)
                 if f.severity == SEV_BLOCK]
        corrupt_turns = [t["turn"] for t in landed if t["verdict"] == "CORRUPT"]
        if cross and not corrupt_turns:
            rec["cross_turn_damage"] = True
            codes |= {"CROSS_TURN_" + f.code for f in cross}
            rec["detail"] = cross[0].detail[:300]

        rec["codes"] = sorted(codes)
        rec["verdict"] = ("CORRUPT"
                          if corrupt_turns or rec.get("cross_turn_damage")
                          else "OK")
        if corrupt_turns:
            rec["first_corrupt_turn"] = corrupt_turns[0]
            bad = next(t for t in landed if t["verdict"] == "CORRUPT")
            rec["detail"] = bad.get("detail", "")
            rec["actual"] = bad.get("line", "")
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["verdict"] = "ERROR"
        rec["codes"] = []
        rec["error"] = repr(exc)[:300]
        return rec
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# A Claude Code COLLABORATOR asked in #14131 on 2026-05-31:
#   "This is expected to occur less frequently when the language is specified
#    in Claude Code settings. I would like to know if folks have that specified
#    and it still repro's, or if it resolves it."
# Three months later the thread has two opinions and no measurements in reply.
# --language answers exactly that question: same prompt, same model, same
# everything, with the setting present or absent.
LANGUAGE_NAMES = {"de": "German", "tr": "Turkish", "es": "Spanish",
                  "fr": "French", "nb": "Norwegian", "pl": "Polish",
                  "cs": "Czech", "ja": "Japanese", "ru": "Russian",
                  "zh": "Chinese"}


def one_run(lang: str, mode: str, model: str, timeout: int,
            language_setting: bool = False, turns: int = 6,
            per_turn: int = 3) -> dict:
    """One isolated attempt. Returns a record; never raises."""
    if mode == "session":
        return one_session_run(lang, model, timeout, language_setting,
                               turns, per_turn)
    workdir = tempfile.mkdtemp(prefix="mgrepro-")
    rec = {"lang": lang, "mode": mode, "model_requested": model,
           "language_setting": language_setting}
    text = intended_for(lang, mode)
    try:
        prompt = PROMPTS[mode].format(out=OUT_NAME, text=text)
        argv = [CLAUDE_BIN] + (CHAT_ARGS if mode == "chat" else CLAUDE_ARGS)
        if language_setting:
            sp = os.path.join(workdir, "mg-settings.json")
            with open(sp, "w", encoding="utf-8") as f:
                json.dump({"language": LANGUAGE_NAMES[lang]}, f)
            argv += ["--settings", sp]
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

        if mode == "chat":
            actual = meta.get("result") or ""
            if not actual.strip():
                rec["verdict"] = "NOFILE"
                rec["codes"] = []
                rec["stderr_tail"] = (proc.stderr or "")[-300:]
                return rec
            findings = [f for f in analyse(text, actual)
                        if f.severity == SEV_BLOCK]
            rec["codes"] = [f.code for f in findings]
            rec["verdict"] = "CORRUPT" if findings else "OK"
            if findings:
                rec["detail"] = findings[0].detail[:400]
                rec["actual"] = actual[:900]
            return rec

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


def run_matrix(langs, runs, model, timeout, mode="plain",
               language_setting=False, verbose=True, turns=6,
               per_turn=3) -> dict:
    started = datetime.now(timezone.utc)
    version = claude_version()
    per_lang = OrderedDict()
    all_records = []

    for lang in langs:
        results = []
        for i in range(runs):
            rec = one_run(lang, mode, model, timeout, language_setting,
                          turns, per_turn)
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
                 if k in ("verdict", "codes", "detail", "actual", "seconds",
                          "first_corrupt_turn", "cross_turn_damage")}
                for r in results if r["verdict"] != "OK"
            ],
        }
        if mode == "session":
            # The point of this mode: corruption by turn index, not by run.
            # attempts counts only turns that actually landed an edit, so a
            # skipped turn never inflates or deflates the rate.
            depth = OrderedDict()
            for r in results:
                for t in r.get("turns", []):
                    if t["verdict"] not in ("OK", "CORRUPT"):
                        continue
                    d = depth.setdefault(t["turn"], {"attempts": 0, "corrupt": 0})
                    d["attempts"] += 1
                    d["corrupt"] += t["verdict"] == "CORRUPT"
            per_lang[lang]["turn_depth"] = {str(k): v for k, v in
                                            sorted(depth.items())}
            per_lang[lang]["skipped_turns"] = sum(
                1 for r in results for t in r.get("turns", [])
                if t["verdict"] == "SKIPPED")
            per_lang[lang]["cross_turn_damage"] = sum(
                1 for r in results if r.get("cross_turn_damage"))
            per_lang[lang]["threads_broken"] = sum(
                1 for r in results if r.get("one_thread") is False)
        if verbose:
            print(" -> %d/%d corrupt" % (per_lang[lang]["corrupt"], runs))
            if mode == "session":
                td = per_lang[lang].get("turn_depth", {})
                print("     by turn: " + " ".join(
                    "t%s %d/%d" % (k, v["corrupt"], v["attempts"])
                    for k, v in td.items()))

    resolved = Counter(r.get("model_resolved") for r in all_records
                       if r.get("model_resolved"))
    cost = sum(r.get("cost_usd") or 0 for r in all_records)

    row = {
        "utc": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "claude_code": version,
        "model_requested": model or "(default)",
        "model_resolved": resolved.most_common(1)[0][0] if resolved else "unknown",
        "os": "%s %s" % (platform.system(), platform.release()),
        "python": platform.python_version(),
        "runs_per_lang": runs,
        "mode": mode,
        "language_setting": language_setting,
        "prompt": ledger_prompt(mode),
        "claude_args": SESSION_ARGS if mode == "session" else CLAUDE_ARGS,
        "languages": per_lang,
        "total_cost_usd": round(cost, 4),
    }
    if mode == "session":
        row["turns"] = turns
        row["words_per_turn"] = per_turn
    return row


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
        "Each cell is `corrupted / runs`. For every mode but `session`, one run",
        "is one headless Claude Code request in an empty directory, with what",
        "came back compared to the intended text codepoint by codepoint.",
        "A `sessionxN` row is different: one run is N small Edit turns inside",
        "ONE session, each turn checked on its own, and the cell counts a run as",
        "corrupt if any of its turns was.",
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
        mode_cell = r.get("mode", "strict")
        if r.get("turns"):
            mode_cell += "x%d" % r["turns"]
        if r.get("language_setting"):
            mode_cell += "+lang"
        out.append("| %s | %s | %s | %s | %s | %d | %s |" % (
            r["utc"][:10], r["claude_code"],
            r.get("model_requested") or r["model_resolved"], r["os"],
            mode_cell, r["runs_per_lang"], " | ".join(cells)))

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
            "python3 repro.py --runs 3 --mode session --turns 6 --langs de",
            "```", ""]

    # Turn depth only exists in session rows, and it is the whole reason that
    # mode was added, so it does not belong squashed into a single cell.
    sess = [r for r in rows if r.get("mode") == "session"]
    if sess:
        out += ["", "## Session mode: corruption by turn depth", "",
                "`n/m` = corrupt / turns that actually landed an edit. A turn",
                "that never landed is counted in neither.", "",
                "| date | Claude Code | OS | lang | " +
                " | ".join("t%d" % i for i in range(1, 1 + max(
                    r.get("turns", 0) for r in sess))) +
                " | skipped | cross-turn |",
                "|---|---|---|---|" + "---|" * (max(
                    r.get("turns", 0) for r in sess) + 2)]
        width = max(r.get("turns", 0) for r in sess)
        for r in sess:
            for lang, d in r["languages"].items():
                td = d.get("turn_depth", {})
                cells = []
                for i in range(1, width + 1):
                    v = td.get(str(i))
                    cells.append("–" if not v else
                                 ("**%d/%d**" if v["corrupt"] else "%d/%d")
                                 % (v["corrupt"], v["attempts"]))
                out.append("| %s | %s | %s | %s | %s | %d | %d |" % (
                    r["utc"][:10], r["claude_code"], r["os"], lang,
                    " | ".join(cells), d.get("skipped_turns", 0),
                    d.get("cross_turn_damage", 0)))

    os.makedirs(RESULTS_DIR, exist_ok=True)
    text = "\n".join(out) + "\n"
    with open(MATRIX_MD, "w", encoding="utf-8") as f:
        f.write(text)
    return text


def pin_stdout_utf8() -> None:
    """Print UTF-8 whatever the console's code page is.

    Found on a ja-JP Windows console, 2026-09-13, by running --render there for
    the first time: the run had already written MATRIX.md correctly, then died
    printing it, because `–` (U+2013) has no CP932 encoding.

    The cosmetic half of that is the table. The half that mattered is 30 lines
    below: the final result is printed with ensure_ascii=False, and `detail`
    and `actual` only carry non-ASCII WHEN A RUN WAS CORRUPT. So on the one
    console this project exists to serve, the harness would have crashed at
    exactly the moment it finally caught something, and every clean run before
    it would have looked fine. It was never hit because every Windows run so
    far reported zero failures.

    A tool that measures encoding loss must not be the thing that loses.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # redirected to something that cannot be reconfigured


def main() -> int:
    pin_stdout_utf8()
    ap = argparse.ArgumentParser(prog="repro.py")
    ap.add_argument("--runs", type=int, default=5, help="runs per language")
    ap.add_argument("--langs", nargs="+", default=list(SAMPLES),
                    choices=list(SAMPLES))
    ap.add_argument("--timeout-long", action="store_true",
                    help=argparse.SUPPRESS)
    ap.add_argument("--model", default="", help="passed to claude --model")
    ap.add_argument("--mode", default="plain", choices=list(PROMPTS),
                    help="strict = told not to alter; plain = no instruction; "
                         "compose = the model writes the words itself; "
                         "long = same, but 700+ words and 20 required words; "
                         "session = many small Edit turns in ONE session, "
                         "checked per turn")
    ap.add_argument("--turns", type=int, default=6,
                    help="session mode: Edit turns inside one session "
                         "(the author of #14131 reports 3-5 is enough)")
    ap.add_argument("--words-per-turn", type=int, default=3,
                    help="session mode: required words per turn")
    ap.add_argument("--language", action="store_true",
                    help="set the Claude Code `language` setting for the run "
                         "(answers the question asked in #14131)")
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

    if args.mode == "session" and args.turns < 1:
        print("--turns must be at least 1", file=sys.stderr)
        return 2

    row = run_matrix(args.langs, args.runs, args.model, args.timeout,
                     args.mode, args.language, turns=args.turns,
                     per_turn=args.words_per_turn)
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
