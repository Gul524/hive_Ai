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
ROMAN_COMMON = {roman: urdu for urdu, roman in COMMON.items()}
ROMAN_COMMON.update({
    "assalam": "السلام", "alaikum": "علیکم", "salam": "سلام",
    "shukriya": "شکریہ", "theek": "ٹھیک", "acha": "اچھا",
    "aapka": "آپ کا", "aapki": "آپ کی", "batao": "بتاؤ",
    "batayein": "بتائیں", "chahiye": "چاہیے", "madad": "مدد",
    "kar": "کر", "sakte": "سکتے", "sakti": "سکتی", "ho": "ہو",
    "hum": "ہم", "yeh": "یہ", "woh": "وہ", "ab": "اب",
    "khush": "خوش", "amdeed": "آمدید", "din": "دن", "raat": "رات",
    "ji": "جی", "haan": "ہاں", "nahi": "نہیں", "zaroor": "ضرور",
})
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


def roman_to_urdu(text: str) -> str:
    """Convert common Roman Urdu words before feeding an Urdu-script voice."""
    return re.sub(r"[A-Za-z]+", lambda match: ROMAN_COMMON.get(
        match.group(0).lower(), match.group(0)), text)
