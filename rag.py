"""Pure-Python BM25 retrieval over the SOPHIA manuals (no embedding API).

Put the SOPHIA User/Theory Guide PDFs (or .txt/.md extracts) in ``manuals/``.
They are lab documents and are NOT included in the public repository.

BM25 score of document d for query q:

    score(d, q) = sum_t IDF(t) * f(t,d) (k1 + 1) / ( f(t,d) + k1 (1 - b + b |d| / avgdl) )
    IDF(t)      = ln( (N - n_t + 0.5) / (n_t + 0.5) + 1 )
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from typing import List, Tuple

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[가-힣]+")


def tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def _read_pdf(path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return ""


def _chunks(text: str, size: int = 900, overlap: int = 150) -> List[str]:
    text = re.sub(r"[ \t]+", " ", text)
    out, i = [], 0
    while i < len(text):
        out.append(text[i:i + size])
        i += size - overlap
    return [c for c in out if c.strip()]


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: List[Tuple[str, str]] = []  # (source, text)
        self.tfs: List[Counter] = []
        self.df: Counter = Counter()
        self.avgdl = 0.0

    def add(self, source: str, text: str) -> None:
        for chunk in _chunks(text):
            toks = tokenize(chunk)
            if not toks:
                continue
            tf = Counter(toks)
            self.docs.append((source, chunk))
            self.tfs.append(tf)
            self.df.update(tf.keys())
        total = sum(sum(tf.values()) for tf in self.tfs)
        self.avgdl = total / max(len(self.tfs), 1)

    def search(self, query: str, k: int = 5) -> List[Tuple[float, str, str]]:
        q = tokenize(query)
        n = len(self.docs)
        if not n or not q:
            return []
        scores = []
        for i, tf in enumerate(self.tfs):
            dl = sum(tf.values())
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                idf = math.log((n - self.df[t] + 0.5) / (self.df[t] + 0.5) + 1.0)
                s += idf * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        return [(s, self.docs[i][0], self.docs[i][1]) for s, i in scores[:k]]

    def context(self, query: str, k: int = 5, max_chars: int = 6000) -> str:
        parts, used = [], 0
        for s, src, txt in self.search(query, k):
            block = f"[{os.path.basename(src)} | score={s:.2f}]\n{txt.strip()}\n"
            if used + len(block) > max_chars:
                break
            parts.append(block)
            used += len(block)
        return "\n".join(parts)


def load_manuals(folder: str) -> BM25Index:
    idx = BM25Index()
    if not os.path.isdir(folder):
        return idx
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        low = name.lower()
        if low.endswith(".pdf"):
            idx.add(path, _read_pdf(path))
        elif low.endswith((".txt", ".md")) and low != "readme.md":
            with open(path, encoding="utf-8", errors="ignore") as fh:
                idx.add(path, fh.read())
    return idx
