"""Agent-facing tool functions. Docstrings are the LLM's tool descriptions (Turkish)."""
import logging

from . import speaker_history, speaker_store, voice_trust
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


def get_speaker_status(tool_context) -> dict:
    """Kadir'in ses kimliği durumunu getirir: bu oturumda ses eşleşmesi var mı, son
    skorlar, ses profili özeti. 'Beni tanıyor musun / ses tanıma var mı' tarzı
    sorularda çağır."""
    try:
        signals = voice_trust.lookup(tool_context)
        user_id = tool_context.session.user_id
        profile = speaker_store.load_profile(_memory.db, user_id)
        history = speaker_history.load_history(_memory.db, user_id)

        canli = signals is not None
        skor = signals.voice_score if canli else None
        if canli and skor is not None:
            aciklama = f"Bu oturumda Kadir'in sesi %{skor * 100:.0f} eşleşmeyle doğrulandı."
        elif canli:
            aciklama = "Sesli oturum açık ama henüz doğrulanmış söyleyiş yok."
        else:
            aciklama = "Bu kanal metin — canlı ses kanıtı yok; profil özeti yine de geçerli."

        return {
            "canli_ses_kanali": canli,
            "guven_seviyesi": signals.trust_level if canli else None,
            "son_eslesme_skoru": skor,
            "cihaz": signals.device_hint if canli else None,
            "presence": signals.presence if canli else None,
            "profil": {
                "capa": len(profile.anchors),
                "otomatik": sum(1 for s in profile.adaptive if s["source"] == "auto"),
                "elle": sum(1 for s in profile.adaptive if s["source"] == "manual"),
            },
            "son_dogrulamalar": [
                {"ts": e.get("ts"), "skor": e.get("score"), "tanindi": e.get("verified")}
                for e in history[-5:]
            ],
            "aciklama": aciklama,
        }
    except Exception:
        logging.exception("get_speaker_status: ses kimliği durumu okunamadı")
        return {"hata": "ses kimliği durumu şu an okunamıyor"}


ALL_TOOLS = [get_user_profile, update_user_profile, remember_fact, add_lesson, search_memory,
             get_speaker_status]
