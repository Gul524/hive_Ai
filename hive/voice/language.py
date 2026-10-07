"""Small deterministic English/Roman Urdu hint for model prompts and errors."""

import re

from hive.core.models import LanguageCode

ROMAN_URDU_WORDS = {
    "karo", "karna", "kar", "hai", "hain", "nahi", "mujhe", "mera", "meri",
    "aap", "tum", "kaise", "kya", "kyun", "chahiye", "batao", "dikhao",
    "aur", "mein", "main", "se", "ko", "ka", "ki", "ke", "theek", "shuru",
}


def detect_language(text: str) -> LanguageCode:
    if re.search(r"[\u0600-\u06ff]", text):
        return LanguageCode.UR_ROMAN
    words = set(re.findall(r"[a-z]+", text.lower()))
    strong = {"karo", "karna", "mujhe", "aap", "kaise", "kyun", "chahiye", "batao", "dikhao"}
    return LanguageCode.UR_ROMAN if (words & strong or len(words & ROMAN_URDU_WORDS) >= 2) else LanguageCode.EN
