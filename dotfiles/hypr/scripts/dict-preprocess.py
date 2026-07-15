#!/usr/bin/env python3
"""Build ~/.local/share/dictcc/de-en.tsv from a raw dict.cc export.

Usage: dict-preprocess.py <raw-dictcc-export.txt>

Output columns: DE, EN, subject tags, word class, DE score, EN score.
Each side's score is the log10 corpus frequency of its rarest content
word (scaled x100), looked up in the FrequencyWords lists (de_full.txt /
en_full.txt in ~/.local/share/dictcc/). The lookup script sorts results
by the score of the side opposite the matched query, so translations of
a word are ordered by how common they are in the target language.
"""

import html
import math
import re
import sys
from pathlib import Path

DATA_DIR = Path.home() / ".local/share/dictcc"

BRACKETS = re.compile(r"\[[^\]]*\]|\{[^}]*\}|<[^>]*>")
NON_WORD = re.compile(r"[^a-zA-ZäöüßÄÖÜáéíóúàèìòùâêîôûñç'-]+")
GENUS = re.compile(r" ?\{(?:m|f|n)\}")


def load_freq(path):
    freq = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            word, _, count = line.rstrip("\n").partition(" ")
            if count:
                freq[word] = int(math.log10(int(count)) * 100)
    return freq


def score(field, freq, skip_to=False):
    words = NON_WORD.split(BRACKETS.sub(" ", field).lower())
    words = [w for w in words if len(w) > 1]
    if skip_to and words and words[0] == "to":
        words = words[1:]
    if not words:
        return 0
    return min(freq.get(w, 0) for w in words)


def main():
    src = sys.argv[1]
    de_freq = load_freq(DATA_DIR / "de_full.txt")
    en_freq = load_freq(DATA_DIR / "en_full.txt")

    n = 0
    with open(src, encoding="utf-8") as f, \
         open(DATA_DIR / "de-en.tsv", "w", encoding="utf-8") as out:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            parts = html.unescape(line.rstrip("\n")).split("\t")
            if len(parts) < 2:
                continue
            de, en = GENUS.sub("", parts[0].strip()), parts[1].strip()
            if not de or not en:
                continue
            wclass = parts[2].strip() if len(parts) > 2 else ""
            tags = parts[3].strip() if len(parts) > 3 else ""
            ds = score(de, de_freq)
            es = score(en, en_freq, skip_to=True)
            out.write(f"{de}\t{en}\t{tags}\t{wclass}\t{ds}\t{es}\n")
            n += 1
    print(f"wrote {n} entries to {DATA_DIR / 'de-en.tsv'}")


if __name__ == "__main__":
    main()
