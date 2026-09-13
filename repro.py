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
    # ★NO instruction about characters. That is the entire point: see the
    # ★NATURAL_WATCH comment above. The document is already on disk, so the
    # ★intended spelling is known without telling the model to preserve it.
    "natural": (
        "Read ./{out} and append a short section at the end of the file that\n"
        "summarises the document and names its key terms.\n"
        "Write in the same language as the document.\n"
        "When the file is written, reply with just: DONE\n"
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

# natural mode appends to a file that already exists.
NATURAL_ARGS = [
    "-p",
    "--output-format", "json",
    "--permission-mode", "acceptEdits",
    "--allowedTools", "Read,Edit,Write",
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
    if mode == "natural":
        return natural_doc(lang)
    if mode in ("long", "chat", "session"):
        return " ".join(LONG_WORDS[lang])
    return SAMPLES[lang]


def ledger_prompt(mode: str) -> str:
    """The prompt, with the sample blanked, as recorded in the ledger."""
    if mode == "session":
        return PROMPTS[mode].format(out=SESSION_FILE, marker="TURN-NN",
                                    text="<SAMPLE>")
    if mode == "natural":
        return PROMPTS[mode].format(out=NATURAL_FILE)
    return PROMPTS[mode].format(out=OUT_NAME, text="<SAMPLE>")


# ------------------------------------------------------------ session mode

# Prose that is ALREADY in the file before the session starts. Written by this
# harness, never by the model, so any character missing from it later was
# destroyed by an edit rather than never generated.
#
# Why this exists: with the pure-ASCII seed, every turn rewrites a line that
# holds no non-ASCII yet, and the only text under test is what the model just
# produced. People who hit this bug are not editing blank lines - they are
# editing files that already contain their language. A read-modify-write over
# existing non-ASCII is a different code path from appending new text, and it
# was untested until this seed existed.
SEED_PROSE = {
    "de": ["Die Verzögerung bei der Prüfung führt zu größeren Ausfällen.",
           "Der Schlüssel liegt in der Qualität der Rückgabe.",
           "Änderungen an der Ausführung brauchen eine Bestätigung."],
    "tr": ["Gecikme, güvenlik açısından büyük sorunlara yol açıyor.",
           "İstanbul'daki sunucu değişikliği doğrulama bekliyor.",
           "Öğrenci kaydı için bağlantı ayarları güncellendi."],
    "es": ["La configuración añade una señal de versión.",
           "¿Cuántos años lleva esta aplicación en producción?",
           "La información de conexión se revisó según el código."],
    "fr": ["La requête déjà prête a réussi l'épreuve de sécurité.",
           "L'élève a détaillé le problème précédent à Noël.",
           "La génération des références demande une vérité vérifiée."],
    "nb": ["Åse og Øyvind spiste smørbrød i Ålesund før møtet.",
           "Løsningen påvirker både årsak og økning i utførelsen.",
           "Første tilbakemelding bør håndteres nødvendig raskt."],
    "pl": ["Zażółć gęślą jaźń, bo wdrożenie ma opóźnienie.",
           "Użytkownik zgłosił błąd w części połączenia.",
           "Wartość źródła można sprawdzić wcześniej i zapisać."],
    "cs": ["Příliš žluťoučký kůň úpěl ďábelské ódy.",
           "Uživatel hlásí chybu v nastavení přístupu.",
           "Hodnota řetězce se změní až po návratu úrovně."],
    "ja": ["文字化けが起きるとファイルが黙って壊れる。",
           "設定を確認してから実行し、出力を計測する。",
           "境界で符号が変わる箇所を再現し、台帳に記録する。"],
    "ru": ["Проверка кодировки показала ошибку при записи.",
           "Настройка значения выполняется после подключения.",
           "Уровень объёма строки обновляется при возврате."],
    "zh": ["这是一个中文测试文件，编码错误会导致数据丢失。",
           "配置监控与回滚需要先验证输出和输入。",
           "变更记录在检查后恢复到原来的设置。"],
}


# ---------------------------------------------------------- natural mode
#
# Every other mode hands the model a word list and says "use these exactly as
# written, do not inflect, decline, conjugate or otherwise change them".
#
# That clause is not decoration either: it exists because Czech declined the
# required word and the codepoint check read the grammar as corruption. It
# made the oracle sound.
#
# It may also have been suppressing the thing under test. If #14131 is the
# model transliterating German of its own accord, then instructing it not to
# change the words is the single most reliable way to stop it -- and a hundred
# clean runs measured under that instruction say much less than they appear to.
#
# So this mode gives NO instruction about characters at all. It puts a German
# document on disk and asks for a summary. The intended spelling is then known
# from the FILE rather than from the prompt, and nothing tells the model to
# preserve it.
#
# ★ The price is that the check is no longer a plain codepoint comparison. It
# ★ looks for a seed word's ASCII-folded form in the new text, which is a
# ★ HEURISTIC, unlike every other mode here. Its false-positive class is
# ★ obvious: a fold that is itself a real German word. "schön" folds to
# ★ "schon", which means "already"; "Größe" folds to "Grosse", which is the
# ★ ordinary Swiss spelling; "Ausfällen" folds to "Ausfallen". Those forms are
# ★ excluded below, per form and not per word, so "für" can still be watched
# ★ through "fuer" while "fur" is ignored.
# ★ A hit from this mode is a LEAD, not a reproduction. Read the sentence.

NATURAL_DOC = {
    "de": [
        "# Betriebshandbuch",
        "",
        "## Auslieferung",
        "",
        "Die Verzögerung bei der Prüfung entsteht, weil die Bestätigung der",
        "Änderung erst nach der Überprüfung durch die Behörde vorliegt. Der",
        "Schlüssel zur Qualität liegt in der Ausführung der Rückgabe.",
        "",
        "## Betrieb",
        "",
        "Das Gebäude meldet die Erhöhung der Vergütung an die Übersicht.",
        "Für das Zurücksetzen können die Werte aus dem Wörterbuch gelesen",
        "werden. Die Prüfung schöner Ausgaben erfolgt getrennt.",
    ],
}

# (word, folded form, code). Only forms whose fold is NOT itself a real word.
NATURAL_WATCH = {
    "de": [
        ("Verzögerung", "Verzoegerung", "ASCII_SUBSTITUTION"),
        ("Verzögerung", "Verzogerung", "SILENT_DROP"),
        ("Prüfung", "Pruefung", "ASCII_SUBSTITUTION"),
        ("Prüfung", "Prufung", "SILENT_DROP"),
        ("Schlüssel", "Schluessel", "ASCII_SUBSTITUTION"),
        ("Schlüssel", "Schlussel", "SILENT_DROP"),
        ("Qualität", "Qualitaet", "ASCII_SUBSTITUTION"),
        ("Qualität", "Qualitat", "SILENT_DROP"),
        ("Rückgabe", "Rueckgabe", "ASCII_SUBSTITUTION"),
        ("Rückgabe", "Ruckgabe", "SILENT_DROP"),
        ("Bestätigung", "Bestaetigung", "ASCII_SUBSTITUTION"),
        ("Bestätigung", "Bestatigung", "SILENT_DROP"),
        ("Ausführung", "Ausfuehrung", "ASCII_SUBSTITUTION"),
        ("Ausführung", "Ausfuhrung", "SILENT_DROP"),
        ("Änderung", "Aenderung", "ASCII_SUBSTITUTION"),
        ("Änderung", "Anderung", "SILENT_DROP"),
        ("Überprüfung", "Ueberpruefung", "ASCII_SUBSTITUTION"),
        ("Überprüfung", "Uberprufung", "SILENT_DROP"),
        ("Vergütung", "Verguetung", "ASCII_SUBSTITUTION"),
        ("Vergütung", "Vergutung", "SILENT_DROP"),
        ("Behörde", "Behoerde", "ASCII_SUBSTITUTION"),
        ("Behörde", "Behorde", "SILENT_DROP"),
        ("Wörterbuch", "Woerterbuch", "ASCII_SUBSTITUTION"),
        ("Wörterbuch", "Worterbuch", "SILENT_DROP"),
        ("Gebäude", "Gebaeude", "ASCII_SUBSTITUTION"),
        ("Gebäude", "Gebaude", "SILENT_DROP"),
        ("Zurücksetzen", "Zuruecksetzen", "ASCII_SUBSTITUTION"),
        ("Zurücksetzen", "Zurucksetzen", "SILENT_DROP"),
        ("Übersicht", "Uebersicht", "ASCII_SUBSTITUTION"),
        ("Übersicht", "Ubersicht", "SILENT_DROP"),
        ("Erhöhung", "Erhoehung", "ASCII_SUBSTITUTION"),
        ("Erhöhung", "Erhohung", "SILENT_DROP"),
        # fold-only: the dropped form of each of these IS a German word
        # ("fur", "konnen", "schon"), so only the ae/oe/ue expansion is watched.
        ("für", "fuer", "ASCII_SUBSTITUTION"),
        ("Für", "Fuer", "ASCII_SUBSTITUTION"),
        ("können", "koennen", "ASCII_SUBSTITUTION"),
        ("schöner", "schoener", "ASCII_SUBSTITUTION"),
    ],
}

NATURAL_FILE = "handbuch.md"


def natural_doc(lang: str) -> str:
    return "\n".join(NATURAL_DOC[lang]) + "\n"


def natural_findings(lang: str, seed: str, added: str) -> list:
    """Folded forms of seed words that turned up in the newly written text.

    A form is only counted if it is absent from the seed, so a document that
    legitimately spells a word that way can never trigger it.
    """
    hits = []
    for word, folded, code in NATURAL_WATCH[lang]:
        if word not in seed or folded in seed:
            continue
        if folded in added:
            i = added.find(folded)
            hits.append({"word": word, "wrote": folded, "code": code,
                         "context": added[max(0, i - 60):i + 60]})
    return hits


def session_seed(turns: int, lang: str = "de", prose: bool = False) -> str:
    """The file the session starts from.

    prose=False: pure ASCII on purpose. Nothing in it can be corrupted, so
    every non-ASCII character found later was produced by the model during the
    session. This isolates generation.

    prose=True: the file already contains the language. Now there are two
    things that can go wrong, and they are different bugs: the model can fail
    to produce a character, or an edit can destroy a character that was already
    on disk. Only the second is a file-handling bug, and the pure-ASCII seed
    could not see it at all.
    """
    lines = ["# Release notes", ""]
    if prose:
        lines += SEED_PROSE[lang] + [""]
    lines += ["- TURN-%02d: TODO" % k for k in range(1, turns + 1)]
    return "\n".join(lines) + "\n"


def seed_prose_text(lang: str, prose: bool) -> str:
    return " ".join(SEED_PROSE[lang]) if prose else ""


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


def one_natural_run(lang: str, model: str, timeout: int,
                    language_setting: bool) -> dict:
    """One request, no instruction about characters at all.

    Verdicts differ from every other mode on purpose:
      OK      nothing folded turned up
      LEAD    a fold appeared -- READ THE SENTENCE before calling it anything
      NOFILE  the model appended nothing to check
    """
    workdir = tempfile.mkdtemp(prefix="mgnat-")
    rec = {"lang": lang, "mode": "natural", "model_requested": model,
           "language_setting": language_setting}
    path = os.path.join(workdir, NATURAL_FILE)
    seed = natural_doc(lang)
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(seed)

        argv = [CLAUDE_BIN] + NATURAL_ARGS
        if language_setting:
            sp = os.path.join(workdir, "mg-settings.json")
            with open(sp, "w", encoding="utf-8") as f:
                json.dump({"language": LANGUAGE_NAMES[lang]}, f)
            argv += ["--settings", sp]
        if model:
            argv += ["--model", model]

        t0 = time.time()
        proc = subprocess.run(argv, input=PROMPTS["natural"].format(
            out=NATURAL_FILE), capture_output=True, encoding="utf-8",
            errors="replace", cwd=workdir, timeout=timeout)
        rec["seconds"] = round(time.time() - t0, 1)

        meta = {}
        try:
            meta = json.loads(proc.stdout or "{}")
        except ValueError:
            pass
        usage = meta.get("modelUsage") or {}
        rec["model_resolved"] = (
            max(usage.items(), key=lambda kv: kv[1].get("outputTokens", 0))[0]
            if usage else "unknown")
        rec["cost_usd"] = meta.get("total_cost_usd")

        with open(path, "rb") as f:
            now = f.read().decode("utf-8", errors="replace")

        # Only the text that was NOT there before is under test. Everything the
        # model merely left alone is the seed's own spelling, not its choice.
        added = now.replace(seed, "") if seed in now else now[len(seed):]
        rec["added_chars"] = len(added.strip())
        if len(added.strip()) < 40:
            rec["verdict"] = "NOFILE"
            rec["codes"] = []
            rec["stderr_tail"] = (proc.stderr or "")[-200:]
            return rec

        # ★The seed itself must still be intact, or "what the model added" is
        # ★not a meaningful slice and every count below is unreliable.
        seed_bad = [f for f in analyse(seed, now) if f.severity == SEV_BLOCK]
        rec["seed_intact"] = not seed_bad
        if seed_bad:
            rec["seed_damage_detail"] = seed_bad[0].detail[:300]

        hits = natural_findings(lang, seed, added)
        rec["hits"] = hits
        rec["codes"] = sorted({h["code"] for h in hits})
        # How many watched words the model actually reused. Without this a
        # clean result is unreadable: it could mean "no folding" or "the
        # summary happened not to use any of the words".
        watched = {w for w, _, _ in NATURAL_WATCH[lang] if w in seed}
        rec["watched_words"] = len(watched)
        rec["reused_words"] = sorted(w for w in watched if w in added)
        rec["verdict"] = "LEAD" if hits else "OK"
        if hits:
            rec["detail"] = "%s -> %s" % (hits[0]["word"], hits[0]["wrote"])
            rec["actual"] = hits[0]["context"]
        rec["added"] = added.strip()[:700]
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


def one_session_run(lang: str, model: str, timeout: int,
                    language_setting: bool, turns: int, per_turn: int,
                    prose_seed: bool = False) -> dict:
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
           "turns_requested": turns, "words_per_turn": per_turn,
           "prose_seed": prose_seed}
    path = os.path.join(workdir, SESSION_FILE)
    prose = seed_prose_text(lang, prose_seed)
    try:
        seed = session_seed(turns, lang, prose_seed)
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
        seed_damaged_at = None
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

            # ★Checked on EVERY turn, landed or not. A turn that failed to edit
            # ★its own line can still have rewritten the file and destroyed
            # ★text that was already on disk. That is a file-handling bug, not
            # ★a generation one, and the pure-ASCII seed cannot see it at all.
            if prose:
                seed_bad = [f for f in analyse(prose, now)
                            if f.severity == SEV_BLOCK]
                tr["seed_ok"] = not seed_bad
                if seed_bad and seed_damaged_at is None:
                    seed_damaged_at = k
                    rec["seed_damage_codes"] = [f.code for f in seed_bad]
                    rec["seed_damage_detail"] = seed_bad[0].detail[:300]

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
        rec["seed_damaged_at"] = seed_damaged_at
        if seed_damaged_at is not None:
            codes |= {"SEED_" + c for c in rec.get("seed_damage_codes", [])}

        if not landed:
            # ★A session where nothing landed is NOT a reproduction -- unless
            # ★the pre-existing prose was destroyed anyway, which is worse.
            rec["verdict"] = "CORRUPT" if seed_damaged_at else "NOFILE"
            rec["codes"] = sorted(codes)
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
                          or seed_damaged_at else "OK")
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
            per_turn: int = 3, prose_seed: bool = False) -> dict:
    """One isolated attempt. Returns a record; never raises."""
    if mode == "session":
        return one_session_run(lang, model, timeout, language_setting,
                               turns, per_turn, prose_seed)
    if mode == "natural":
        return one_natural_run(lang, model, timeout, language_setting)
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
               per_turn=3, prose_seed=False) -> dict:
    started = datetime.now(timezone.utc)
    version = claude_version()
    per_lang = OrderedDict()
    all_records = []

    for lang in langs:
        results = []
        for i in range(runs):
            rec = one_run(lang, mode, model, timeout, language_setting,
                          turns, per_turn, prose_seed)
            results.append(rec)
            all_records.append(rec)
            if verbose:
                mark = {"OK": ".", "CORRUPT": "X", "LEAD": "L", "NOFILE": "?",
                        "TIMEOUT": "T", "ERROR": "E"}.get(rec["verdict"], "?")
                sys.stdout.write("%s%s " % (lang if i == 0 else "", mark))
                sys.stdout.flush()
        counts = Counter(r["verdict"] for r in results)
        codes = Counter(c for r in results for c in r.get("codes", []))
        per_lang[lang] = {
            "runs": runs,
            # natural mode never says CORRUPT: a folded form is a LEAD that a
            # human still has to read. Counting it as corruption here would
            # quietly promote a heuristic to a measurement.
            "corrupt": counts.get("CORRUPT", 0) + counts.get("LEAD", 0),
            "leads": counts.get("LEAD", 0),
            "ok": counts.get("OK", 0),
            "nofile": counts.get("NOFILE", 0),
            "timeout": counts.get("TIMEOUT", 0),
            "error": counts.get("ERROR", 0),
            "codes": dict(codes),
            "failures": [
                {k: v for k, v in r.items()
                 if k in ("verdict", "codes", "detail", "actual", "seconds",
                          "first_corrupt_turn", "cross_turn_damage",
                          "hits", "added", "reused_words")}
                for r in results if r["verdict"] != "OK"
            ],
        }
        if mode == "natural":
            per_lang[lang]["reused_words"] = sorted(
                {w for r in results for w in r.get("reused_words", [])})
            per_lang[lang]["watched_words"] = max(
                [r.get("watched_words", 0) for r in results] or [0])
            per_lang[lang]["seed_intact"] = all(
                r.get("seed_intact", True) for r in results)
            per_lang[lang]["hits"] = [h for r in results
                                      for h in r.get("hits", [])]
            per_lang[lang]["samples"] = [r.get("added", "")[:400]
                                         for r in results][:3]
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
            per_lang[lang]["prose_seed"] = prose_seed
            if prose_seed:
                dmg = [r.get("seed_damaged_at") for r in results]
                per_lang[lang]["seed_damaged"] = sum(
                    1 for d in dmg if d is not None)
                per_lang[lang]["seed_damaged_at"] = [d for d in dmg
                                                     if d is not None]
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
        row["prose_seed"] = prose_seed
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
        if r.get("prose_seed"):
            mode_cell += "+prose"
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
        width = max(r.get("turns", 0) for r in sess)
        out += ["", "## Session mode: corruption by turn depth", "",
                "`n/m` = corrupt / turns that actually landed an edit. A turn",
                "that never landed is counted in neither.", "",
                "`seed` is what the file contained before the session started.",
                "`ascii` means nothing on disk could be corrupted, so the",
                "column measures generation only. `prose` means the file",
                "already held the language, and `seed lost` counts runs where",
                "an edit destroyed text that was there from the start — a",
                "file-handling failure, which the `ascii` seed cannot see.", "",
                "| date | Claude Code | OS | lang | seed | " +
                " | ".join("t%d" % i for i in range(1, width + 1)) +
                " | skipped | cross-turn | seed lost |",
                "|---|---|---|---|---|" + "---|" * (width + 3)]
        for r in sess:
            for lang, d in r["languages"].items():
                td = d.get("turn_depth", {})
                cells = []
                for i in range(1, width + 1):
                    v = td.get(str(i))
                    cells.append("–" if not v else
                                 ("**%d/%d**" if v["corrupt"] else "%d/%d")
                                 % (v["corrupt"], v["attempts"]))
                prose = d.get("prose_seed") or r.get("prose_seed")
                lost = ("**%d**" % d["seed_damaged"]
                        if d.get("seed_damaged") else
                        ("%d" % d.get("seed_damaged", 0) if prose else "–"))
                out.append("| %s | %s | %s | %s | %s | %s | %d | %d | %s |" % (
                    r["utc"][:10], r["claude_code"], r["os"], lang,
                    "prose" if prose else "ascii",
                    " | ".join(cells), d.get("skipped_turns", 0),
                    d.get("cross_turn_damage", 0), lost))

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
    ap.add_argument("--prose-seed", action="store_true",
                    help="session mode: start from a file that ALREADY "
                         "contains the language, and check that the "
                         "pre-existing text survives every edit")
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
                     per_turn=args.words_per_turn,
                     prose_seed=args.prose_seed)
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
