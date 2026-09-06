#!/usr/bin/env python3
"""
Measured self test for mojibake-guard.

Two numbers matter and both are counted here, not estimated:

  false positives  clean pairs that produced a blocking finding   must be 0
  detection        corrupted pairs that produced a blocking one   want 100%

A clean pair is any edit that a normal agent really performs: rewriting the
same text, appending, indenting, changing line endings, adding more non-ASCII,
writing "ae" on purpose, re-normalising NFC/NFD. A corrupted pair is one of the
failure modes taken from the real bug reports.
"""

from __future__ import annotations

import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mojibake_guard import ASCII_EXPANSION, SEV_BLOCK, analyse, scan_text  # noqa: E402

# --------------------------------------------------------------- samples

SAMPLES = [
    ("de-umlaut", "Die Verzögerung führt zu größeren Ausfällen. Straße, Übung, schön."),
    ("de-code", 'const msg = "Grüße aus München";\nfunction prüfen() { return "übermäßig"; }'),
    ("tr", "Türkçe karakterler: ışğüşöç İstanbul'da yağmur yağıyor."),
    ("es", "La configuración añade una señal de peligro. ¿Cuántos años? ¡Sí!"),
    ("fr", "Créer une requête déjà prête. L'élève a réussi l'épreuve à Noël."),
    ("nordic", "Åse og Øyvind spiste smørbrød i Ålesund. Räksmörgås på svenska."),
    ("pt", "Configuração de instalação não está funcionando. Ação, coração, irmã."),
    ("ja", "文字化けが起きるとファイルが黙って壊れる。日本語のコメントは危ない。"),
    ("ja-code", 'def 検証(値):\n    """設定を確認する"""\n    return {"結果": 値, "状態": "正常"}'),
    ("ja-braces", "# 設定{ここ}を読む\nconfig = {\"名前\": \"値\"}  # 配列[0]は先頭\\末尾"),
    ("zh", "这是一个中文测试文件。编码错误会导致数据丢失。"),
    ("ko", "한국어 인코딩 테스트입니다. 파일이 조용히 깨집니다."),
    ("ru", "Проверка кодировки. Файл тихо ломается при записи."),
    ("el", "Ελληνικά γράμματα σε αρχείο κειμένου."),
    ("ar", "اختبار الترميز العربي في الملفات النصية."),
    ("he", "בדיקת קידוד עברית בקובץ טקסט."),
    ("hi", "यह हिंदी में एक परीक्षण है। फ़ाइल चुपचाप टूट जाती है।"),
    ("th", "การทดสอบการเข้ารหัสภาษาไทยในไฟล์ข้อความ"),
    ("vi", "Kiểm tra mã hoá tiếng Việt trong tệp văn bản."),
    ("pl", "Zażółć gęślą jaźń — polskie znaki diakrytyczne."),
    ("cz", "Příliš žluťoučký kůň úpěl ďábelské ódy."),
    ("emoji", "Build passed ✅ deploy failed ❌ retry 🔁 — see the log 📄"),
    ("math", "∀x ∈ ℝ: x² ≥ 0 ∧ ∑ᵢ aᵢ ≤ ∞ ⇒ π ≈ 3.14159"),
    ("box", "┌───────────┬────────┐\n│ name      │ value  │\n├───────────┼────────┤\n└───────────┴────────┘"),
    ("quotes", "He said “it works” — she replied ‘no it doesn’t’… (an em–dash, a hyphen)"),
    ("nbsp", "10 000 km/h · 25 °C · 3 €"),
    ("mixed", "設定ファイル config.yaml の Größe が 0 バイト: ¿por qué? ✅"),
    ("ascii-only-ae", "The German word for street is written Strasse in ASCII. Goethe, Mueller, Koeln."),
    ("json-ja", '{"タイトル": "テスト", "本文": "改行\\nと引用\\"を含む", "数": 42}'),
    ("md-ja", "# 見出し\n\n- 箇条書き **太字** `コード`\n- [リンク](https://example.com) と ~~打ち消し~~\n"),
]

# --------------------------------------------------- transforms that are OK

def t_identity(s):
    return s


def t_append_ascii(s):
    return s + "\n\n// appended by a later edit\n"


def t_wrap_in_file(s):
    return "# header\n\n" + s + "\n\n# footer, unrelated content\n"


def t_indent(s):
    return "\n".join("    " + line for line in s.split("\n"))


def t_crlf(s):
    return s.replace("\n", "\r\n")


def t_more_nonascii(s):
    return s + "\n追加された行 — mit Umlauten: Ärger, Größe ✅\n"


def t_reorder(s):
    lines = s.split("\n")
    return "\n".join(lines[::-1])


def t_nfd(s):
    return unicodedata.normalize("NFD", s)


def t_nfc(s):
    return unicodedata.normalize("NFC", s)


def t_trailing_nl(s):
    return s.rstrip("\n") + "\n"


CLEAN_TRANSFORMS = [
    ("identity", t_identity),
    ("append-ascii", t_append_ascii),
    ("wrap-in-file", t_wrap_in_file),
    ("indent", t_indent),
    ("crlf", t_crlf),
    ("add-more-nonascii", t_more_nonascii),
    ("reorder-lines", t_reorder),
    ("normalize-nfd", t_nfd),
    ("normalize-nfc", t_nfc),
    ("trailing-newline", t_trailing_nl),
]

# ------------------------------------------------ transforms that must fire

def c_ascii_expansion(s):
    """de/tr: ä -> ae. The exact behaviour #14131 and #13939 report."""
    for ch, exp in ASCII_EXPANSION.items():
        s = s.replace(ch, exp)
    return s


def c_strip_diacritics(s):
    """es/fr: Verzögerung -> Verzogerung. Combining marks thrown away."""
    d = unicodedata.normalize("NFD", s)
    return "".join(c for c in d if not unicodedata.combining(c))


def c_to_fffd(s):
    """#7335: non-ASCII becomes U+FFFD when the file is written on Windows."""
    return "".join("�" if ord(c) > 127 else c for c in s)


def c_delete_nonascii(s):
    """#14437: the characters are simply gone."""
    return "".join(c for c in s if ord(c) < 128)


def c_nul_inject(s):
    """Reported with box-drawing characters on Windows."""
    return "".join("\x00" if ord(c) > 0x2500 else c for c in s)


def c_cp932_roundtrip(s):
    """Encode as CP932 dropping what does not fit, then read back."""
    return s.encode("cp932", errors="ignore").decode("cp932")


def c_cp932_swallow(s):
    """The measured Japanese failure: the ASCII byte after a CJK run vanishes."""
    out = []
    prev_cjk = False
    for ch in s:
        cjk = 0x3000 <= ord(ch) <= 0x9FFF
        if prev_cjk and ch in "\\{}[]|~^@`":
            prev_cjk = False
            continue  # swallowed
        out.append(ch)
        prev_cjk = cjk
    return "".join(out)


def c_latin1_mojibake(s):
    """UTF-8 bytes read back as Latin-1: ä -> Ã¤."""
    return s.encode("utf-8").decode("latin-1")


def c_truncate_nonascii_tail(s):
    """Write cut short: everything from the last non-ASCII character is lost."""
    idxs = [i for i, c in enumerate(s) if ord(c) > 127]
    return s if len(idxs) < 2 else s[: idxs[len(idxs) // 2]]


CORRUPT_TRANSFORMS = [
    ("ascii-expansion", c_ascii_expansion),
    ("strip-diacritics", c_strip_diacritics),
    ("to-U+FFFD", c_to_fffd),
    ("delete-nonascii", c_delete_nonascii),
    ("nul-inject", c_nul_inject),
    ("cp932-roundtrip", c_cp932_roundtrip),
    ("cp932-swallow", c_cp932_swallow),
    ("latin1-mojibake", c_latin1_mojibake),
    ("truncate-nonascii-tail", c_truncate_nonascii_tail),
]


def really_corrupt(intended, broken):
    """Ground truth, decided without asking the detector.

    A transform only counts as corruption if the result is not Unicode-equivalent
    to the original. NFD("한") is a different codepoint sequence but the same
    text, so a tool that stays quiet about it is right, not wrong.
    """
    if broken == intended:
        return False
    return (unicodedata.normalize("NFC", broken)
            != unicodedata.normalize("NFC", intended))


def blocking(intended, actual):
    return [f for f in analyse(intended, actual) if f.severity == SEV_BLOCK]


def has_nonascii(s):
    return any(ord(c) > 127 for c in s)


# --------------------------------------------------------------- real files

def real_files(roots, limit=1500):
    found = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d != "results"]
            for fn in filenames:
                if not fn.lower().endswith((".md", ".txt", ".py", ".json", ".po",
                                            ".rst", ".ts", ".js", ".html", ".css",
                                            ".yml", ".yaml", ".toml", ".cfg")):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) > 300_000:
                        continue
                    with open(p, "rb") as f:
                        raw = f.read()
                    text = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                if has_nonascii(text) and "�" not in text:
                    found.append((p, text))
                    if len(found) >= limit:
                        return found
    return found


def _real_roots():
    """Places that hold real non-ASCII text, on whichever OS this is.

    The first version of this list was Linux-only, so on Windows the real-file
    corpus came back empty and the suite passed on synthetic samples alone -
    a green result that had measured almost nothing. Found by running it on
    Windows; the numbers below are only as good as the corpus that is present,
    so the report prints the file count and you should check it is not 0.
    """
    import site
    import sysconfig

    roots = [os.environ.get("MG_CORPUS", "")]
    for key in ("stdlib", "purelib", "platlib", "data"):
        try:
            roots.append(sysconfig.get_paths().get(key, ""))
        except Exception:
            pass
    try:
        roots.extend(site.getsitepackages())
    except Exception:
        pass
    roots += [
        "/mnt/user-data/uploads",
        "/usr/lib/node_modules", "/usr/share/doc", "/usr/share/i18n",
        "/usr/share/locale", "/usr/share/perl", "/usr/share/vim",
    ]
    seen, out = set(), []
    for r in roots:
        if r and os.path.isdir(r) and r not in seen:
            seen.add(r)
            out.append(r)
    return out


HERE_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_ROOTS = _real_roots()


# --------------------------------------------------------------- the run

def run_corpus() -> int:
    synth_clean = synth_clean_fp = 0
    fp_examples = []

    for label, text in SAMPLES:
        for tname, fn in CLEAN_TRANSFORMS:
            synth_clean += 1
            found = blocking(text, fn(text))
            if found:
                synth_clean_fp += 1
                fp_examples.append((label, tname, found[0].code))

    synth_bad = synth_bad_missed = 0
    by_mode = {}
    missed_examples = []

    for label, text in SAMPLES:
        for cname, fn in CORRUPT_TRANSFORMS:
            broken = fn(text)
            if not really_corrupt(text, broken):
                continue  # transform did not damage this sample
            synth_bad += 1
            hit, tot = by_mode.get(cname, (0, 0))
            found = blocking(text, broken)
            if found:
                by_mode[cname] = (hit + 1, tot + 1)
            else:
                by_mode[cname] = (hit, tot + 1)
                synth_bad_missed += 1
                missed_examples.append((label, cname))

    # ---- real files: clean side
    files = real_files(REAL_ROOTS)
    real_clean = real_fp = 0
    real_fp_examples = []
    for path, text in files:
        for tname, fn in CLEAN_TRANSFORMS:
            real_clean += 1
            if blocking(text, fn(text)):
                real_fp += 1
                real_fp_examples.append((os.path.basename(path), tname))

    # ---- real files: corrupted side
    real_bad = real_missed = 0
    for path, text in files:
        for cname, fn in CORRUPT_TRANSFORMS:
            try:
                broken = fn(text)
            except Exception:
                continue
            if not really_corrupt(text, broken):
                continue
            real_bad += 1
            if not blocking(text, broken):
                real_missed += 1

    # ---- scan mode on untouched real files (no intended version available)
    scan_files = len(files)
    scan_fp = 0
    scan_fp_examples = []
    for path, text in files:
        if scan_text(text):
            scan_fp += 1
            scan_fp_examples.append(os.path.basename(path))

    scan_bad = scan_missed = 0
    scan_missed_examples = []
    for path, text in files:
        for cname, fn in [("to-U+FFFD", c_to_fffd),
                          ("nul-inject", c_nul_inject),
                          ("latin1-mojibake", c_latin1_mojibake)]:
            broken = fn(text)
            if not really_corrupt(text, broken):
                continue
            scan_bad += 1
            if not scan_text(broken):
                scan_missed += 1
                scan_missed_examples.append((os.path.basename(path), cname))

    # ---------------------------------------------------------- report
    def pct(a, b):
        return "n/a" if not b else "%.3f%%" % (100.0 * a / b)

    print("mojibake-guard self test")
    print("=" * 62)
    print("synthetic corpus  %d samples x %d clean / %d corrupting transforms"
          % (len(SAMPLES), len(CLEAN_TRANSFORMS), len(CORRUPT_TRANSFORMS)))
    print("real files        %d (non-ASCII, decoded clean as UTF-8)%s"
          % (len(files), "   <-- EMPTY: only the synthetic corpus was measured"
             if not files else ""))
    print()
    print("COMPARE MODE (hook: intended vs what landed on disk)")
    print("  clean  synthetic   %6d pairs   false positives %4d  (%s)"
          % (synth_clean, synth_clean_fp, pct(synth_clean_fp, synth_clean)))
    print("  clean  real files  %6d pairs   false positives %4d  (%s)"
          % (real_clean, real_fp, pct(real_fp, real_clean)))
    print("  broken synthetic   %6d pairs   missed          %4d  (detection %s)"
          % (synth_bad, synth_bad_missed,
             pct(synth_bad - synth_bad_missed, synth_bad)))
    print("  broken real files  %6d pairs   missed          %4d  (detection %s)"
          % (real_bad, real_missed, pct(real_bad - real_missed, real_bad)))
    print()
    print("  detection by failure mode")
    for cname, _ in CORRUPT_TRANSFORMS:
        hit, tot = by_mode.get(cname, (0, 0))
        print("    %-18s %3d/%-3d  %s" % (cname, hit, tot, pct(hit, tot)))
    print()
    print("SCAN MODE (advisory: no intended version, budget 5.000%)")
    print("  clean  real files  %6d files   false positives %4d  (%s)"
          % (scan_files, scan_fp, pct(scan_fp, scan_files)))
    print("  broken real files  %6d files   missed          %4d  (detection %s)"
          % (scan_bad, scan_missed, pct(scan_bad - scan_missed, scan_bad)))

    # What must be perfect and what is allowed to be noisy are different
    # questions, so they are judged separately.
    #
    # COMPARE mode is the hook. It blocks a write, so a false positive stops
    # real work: the bar is zero, with no budget.
    #
    # SCAN mode has no intended text to compare against, so it reads a file and
    # guesses. It fires on files that legitimately CONTAIN mojibake as data -
    # charset mapping tables (sbcs-data.js, cp949.json) and the encoding test
    # fixtures in the Python standard library (test_email.py, test_doctest2.txt).
    # Those are true sightings of the byte pattern and a wrong verdict about the
    # file, and no rule separates them without knowing what the file is for.
    # So scan mode is advisory, reports its rate, and is held to a budget.
    SCAN_FP_BUDGET = 0.05
    scan_fp_rate = (scan_fp / scan_files) if scan_files else 0.0
    failed = bool(synth_clean_fp or real_fp or synth_bad_missed or real_missed
                  or scan_missed or scan_fp_rate > SCAN_FP_BUDGET)
    if fp_examples:
        print("\n  FALSE POSITIVES (synthetic):")
        for e in fp_examples[:15]:
            print("    %s / %s -> %s" % e)
    if real_fp_examples:
        print("\n  FALSE POSITIVES (real):")
        for e in real_fp_examples[:15]:
            print("    %s / %s" % e)
    if missed_examples:
        print("\n  MISSED:")
        for e in missed_examples[:15]:
            print("    %s / %s" % e)
    if scan_missed_examples:
        print("\n  SCAN missed:")
        for e in scan_missed_examples[:15]:
            print("    %s / %s" % e)
    if scan_fp_examples:
        print("\n  SCAN false positives (files that contain mojibake as data):")
        for e in scan_fp_examples[:15]:
            print("    %s" % e)

    print()
    print("RESULT:", "FAIL" if failed else "PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_corpus())
