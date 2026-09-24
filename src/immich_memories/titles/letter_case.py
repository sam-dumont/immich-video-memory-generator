"""Capitals the way each script sets them, for titles and captions."""

from __future__ import annotations

import unicodedata

_TONOS = "́"
_DIALYTIKA = "̈"
# A stressed first vowel before one of these was read apart from it ("Μάιος");
# once the stress mark goes, a diaeresis keeps it apart ("ΜΑΪΟΣ").
_DIPHTHONG_FIRST = frozenset("ΑΕΟΥ")
_DIPHTHONG_SECOND = frozenset("ΙΥ")


def _greek(char: str) -> bool:
    return unicodedata.name(char, "").startswith("GREEK")


def display_upper(text: str) -> str:
    """`text` in capitals as a sign or a title prints them.

    Greek capitals carry no stress accent: "Ηράκλειο" is "ΗΡΑΚΛΕΙΟ", where
    `str.upper()` gives "ΗΡΆΚΛΕΙΟ". When the accent was what kept two vowels
    apart, the second takes a diaeresis instead ("Μάιος" is "ΜΑΪΟΣ"). Every
    other script is `str.upper()` unchanged.
    """
    out: list[str] = []
    base = ""
    split_pair = False
    for char in unicodedata.normalize("NFD", text.upper()):
        if not unicodedata.combining(char):
            if split_pair and char in _DIPHTHONG_SECOND:
                out.extend((char, _DIALYTIKA))
            else:
                out.append(char)
            base, split_pair = char, False
        elif char == _TONOS and _greek(base):
            split_pair = base in _DIPHTHONG_FIRST
        elif not (char == _DIALYTIKA and out[-1:] == [_DIALYTIKA]):
            out.append(char)
    return unicodedata.normalize("NFC", "".join(out))
