"""Agent-facing tool functions. Docstrings are the LLM's tool descriptions (Turkish)."""
from .memory import Memory

_memory: Memory | None = None


def init(memory: Memory) -> None:
    global _memory
    _memory = memory


def get_user_profile() -> dict:
    """Kadir'in profilini (tercihler, rutinler, kurallar) getirir. Her oturumun başında çağır."""
    return _memory.get_profile()


def update_user_profile(patch: dict) -> dict:
    """Kadir'in profiline kalıcı bilgi ekler/günceller. Örn: {"coffee": "X kafeden"}."""
    return _memory.update_profile(patch)


def remember_fact(fact: str) -> str:
    """Kadir hakkında öğrenilen tek bir gerçeği kalıcı hafızaya yazar."""
    return _memory.remember_fact(fact)


def add_lesson(context: str, tried: str, went_wrong: str, correct: str) -> str:
    """Bir hata düzeltildiğinde ders kaydeder: bağlam, ne denendi, ne yanlış gitti, doğrusu ne."""
    return _memory.add_lesson(context, tried, went_wrong, correct)


def search_memory(query: str) -> list[dict]:
    """Kalıcı hafızada (gerçekler + dersler) arama yapar."""
    return _memory.search_memory(query)


ALL_TOOLS = [get_user_profile, update_user_profile, remember_fact, add_lesson, search_memory]
