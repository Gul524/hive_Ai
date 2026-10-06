from hive.core.models import LanguageCode
from hive.voice.language import detect_language


def test_language_hint() -> None:
    assert detect_language("nginx install karo aur start karo") == LanguageCode.UR_ROMAN
    assert detect_language("Please install nginx") == LanguageCode.EN
