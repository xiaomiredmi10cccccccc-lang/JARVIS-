"""Text preparation for the Tajik voice (pure Python, no heavy imports)."""
import re

UNITS = ["сифр", "як", "ду", "се", "чор", "панҷ", "шаш", "ҳафт", "ҳашт", "нӯҳ"]
TEENS = {10: "даҳ", 11: "ёздаҳ", 12: "дувоздаҳ", 13: "сенздаҳ", 14: "чордаҳ", 15: "понздаҳ",
         16: "шонздаҳ", 17: "ҳифдаҳ", 18: "ҳаждаҳ", 19: "нуздаҳ"}
TENS = {2: "бист", 3: "сӣ", 4: "чил", 5: "панҷоҳ", 6: "шаст", 7: "ҳафтод", 8: "ҳаштод", 9: "навад"}


def _below_1000(n):
    words = []
    h, r = divmod(n, 100)
    if h:
        words.append("сад" if h == 1 else UNITS[h] + "сад")
    if r >= 20:
        t, u = divmod(r, 10)
        words.append(TENS[t] + ("у " + UNITS[u] if u else ""))
    elif r >= 10:
        words.append(TEENS[r])
    elif r > 0:
        words.append(UNITS[r])
    return " ".join(words)


def number_words(n):
    if n == 0:
        return UNITS[0]
    parts = []
    for size, name in ((10 ** 9, "миллиард"), (10 ** 6, "миллион"), (1000, "ҳазор")):
        q, n = divmod(n, size)
        if q:
            parts.append(("як " if q == 1 else _below_1000(q) + " ") + name)
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def spell_numbers(text):
    def repl(m):
        s = m.group(0)
        if len(s) > 12:
            return " ".join(UNITS[int(c)] for c in s)
        return number_words(int(s))
    text = text.replace("%", " фоиз ")
    return re.sub(r"\d+", repl, text)


def normalize(text, vocab):
    """Lower-case, spell out numbers, keep only characters the model knows (others become spaces)."""
    text = spell_numbers(text.lower())
    out = []
    for ch in text:
        out.append(ch if (ch in vocab or ch == " ") else " ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def split_sentences(text, limit=220):
    """Short pieces (a sentence or less) so each model call stays quick."""
    pieces = [p.strip() for p in re.split(r"(?<=[.!?…])\s+|\n+", text) if p.strip()]
    out = []
    for p in pieces:
        while len(p) > limit:
            cut = p.rfind(" ", 0, limit)
            cut = cut if cut > 40 else limit
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out
