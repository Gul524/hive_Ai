"""Readable offline Urdu-script fallback for Roman Urdu interaction."""

from __future__ import annotations

import re

COMMON = {
    "مجھے": "mujhe", "میں": "main", "میرا": "mera", "میری": "meri",
    "آپ": "aap", "تم": "tum", "کیا": "kya", "کیوں": "kyun",
    "کیسے": "kaise", "ہے": "hai", "ہیں": "hain", "نہیں": "nahi",
    "کرو": "karo", "کرنا": "karna", "اور": "aur", "سے": "se",
    "کو": "ko", "کا": "ka", "کی": "ki", "کے": "ke",
    "انسٹال": "install", "شروع": "shuru", "بند": "band",
}
LETTERS = {
    "ا": "a", "آ": "aa", "ب": "b", "پ": "p", "ت": "t", "ٹ": "t",
    "ث": "s", "ج": "j", "چ": "ch", "ح": "h", "خ": "kh", "د": "d",
    "ڈ": "d", "ذ": "z", "ر": "r", "ڑ": "r", "ز": "z", "ژ": "zh",
    "س": "s", "ش": "sh", "ص": "s", "ض": "z", "ط": "t", "ظ": "z",
    "ع": "a", "غ": "gh", "ف": "f", "ق": "q", "ک": "k", "گ": "g",
    "ل": "l", "م": "m", "ن": "n", "ں": "n", "و": "o", "ہ": "h",
    "ھ": "h", "ء": "", "ی": "i", "ے": "e", "ئ": "i", "ؤ": "u",
}


def urdu_to_roman(text: str) -> str:
    def word(match: re.Match[str]) -> str:
        source = match.group(0)
        return COMMON.get(source, "".join(LETTERS.get(char, char) for char in source))
    return re.sub(r"[\u0600-\u06ff]+", word, text)
