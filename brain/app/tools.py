"""Agent-facing tool functions. Docstrings are the LLM's tool descriptions (Turkish)."""
import logging
import re

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from . import consult, reminders, repo_watch, speaker_history, speaker_store, vitals, voice_trust
from .memory import Memory

_memory: Memory | None = None
_github_client: "repo_watch.GitHubClient | None" = None


def init(memory: Memory) -> None:
    global _memory
    _memory = memory


def _repo_client() -> "repo_watch.GitHubClient":
    """Tembel modül tekili: ilk kullanımda GITHUB_TOKEN env'iyle kurulur."""
    global _github_client
    if _github_client is None:
        _github_client = repo_watch.make_client()
    return _github_client


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


_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def watch_repo(repo: str, note: str = "") -> dict:
    """Bir GitHub repo'sunu ('sahip/repo') takip listesine ekler; yeni release ve
    commit'ler saatlik kontrol edilir. Kadir ilginç bir repo'dan bahsedince notuyla ekle."""
    try:
        repo = repo.strip()
        if not _REPO_NAME_RE.match(repo):
            return {"hata": f"'{repo}' geçerli bir repo adı değil; 'sahip/repo' biçiminde olmalı"}
        try:
            _repo_client().get(f"/repos/{repo}")
        except repo_watch.GitHubError as exc:
            if exc.status == 404:
                return {"hata": f"GitHub'da '{repo}' bulunamadı; izlemeye alınmadı"}
            return {"hata": f"GitHub kontrolü yapılamadı: {exc}"}
        doc = {
            "repo": repo,
            "note": note,
            "added_at": repo_watch._now(),
            # Baseline alanları boş: ilk poll olay üretmez, mevcut durumu kaydeder.
            "last_check": None,
            "last_error": None,
            "release_etag": None,
            "commits_etag": None,
            "last_release_tag": None,
            "last_commit_sha": None,
        }
        try:
            _memory.db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(repo)).create(doc)
        except AlreadyExists:
            return {"hata": f"'{repo}' zaten izleniyor"}
        return {"sonuc": f"'{repo}' izlemeye alındı"}
    except Exception:
        logging.exception("watch_repo: repo izlemeye alınamadı")
        return {"hata": "repo şu an izlemeye alınamıyor"}


def unwatch_repo(repo: str) -> dict:
    """Bir GitHub repo'sunu takip listesinden çıkarır; geçmiş olayları silinmez."""
    try:
        repo = repo.strip()
        ref = _memory.db.collection(repo_watch.WATCH_COLLECTION).document(repo_watch.doc_id(repo))
        if not ref.get().exists:
            return {"hata": f"'{repo}' zaten izlenmiyor"}
        ref.delete()
        return {"sonuc": f"'{repo}' izlemeden çıkarıldı"}
    except Exception:
        logging.exception("unwatch_repo: repo izlemeden çıkarılamadı")
        return {"hata": "repo şu an izlemeden çıkarılamıyor"}


def list_watched_repos() -> dict:
    """Takip edilen GitHub repo'larını durumlarıyla (son kontrol, son hata) listeler."""
    try:
        repos = []
        for snap in _memory.db.collection(repo_watch.WATCH_COLLECTION).stream():
            doc = snap.to_dict()
            repos.append({
                "repo": doc.get("repo"),
                "note": doc.get("note", ""),
                "last_check": doc.get("last_check"),
                "last_error": doc.get("last_error"),
            })
        return {"repolar": repos, "sayi": len(repos)}
    except Exception:
        logging.exception("list_watched_repos: repo listesi okunamadı")
        return {"hata": "repo listesi şu an okunamıyor"}


def get_repo_updates() -> dict:
    """Takip edilen repo'larda Kadir'e henüz gösterilmemiş yenilikleri (yeni release,
    yeni commit'ler) getirir ve gösterildi olarak işaretler. Her oturumun başında çağır."""
    try:
        snaps = list(
            _memory.db.collection(repo_watch.EVENTS_COLLECTION)
            .where(filter=FieldFilter("surfaced", "==", False))
            .stream()
        )
        events = []
        for snap in snaps:
            doc = snap.to_dict()
            events.append({
                "repo": doc.get("repo"),
                "kind": doc.get("kind"),
                "title": doc.get("title"),
                "detail": doc.get("detail"),
                "url": doc.get("url"),
                "ts": doc.get("ts"),
            })
            # Sorgudan hemen sonra işaretle: ikinci çağrı aynı olayları tekrar döndürmez.
            snap.reference.set({"surfaced": True}, merge=True)
        events.sort(key=lambda e: e.get("ts") or "")
        return {"olaylar": events, "sayi": len(events)}
    except Exception:
        logging.exception("get_repo_updates: repo olayları okunamadı")
        return {"hata": "repo yenilikleri şu an okunamıyor"}


def check_my_vitals() -> dict:
    """Kendi sağlık ve kota durumumu raporlar (salt okuma): brain process
    gerçekleri (başlangıç, uptime, revizyon), günlük kullanım sayaçları
    (chat/ses turu, araç çağrısı) ve son politika engelleri. Aylık harcama
    bilinçli null — 30 Temmuz'da metin yolu yerel proxy/aboneliğe geçti,
    faturalı API yok. "Kendini nasıl hissediyorsun", "kota durumun ne",
    "bugün kaç istek aldın", "son hataların ne" sorularında çağır."""
    try:
        return vitals.read(_memory.db)
    except Exception:
        logging.exception("check_my_vitals: vitals okunamadı")
        return {"hata": "sağlık bilgisi şu an okunamıyor"}


def set_reminder(text: str, due_at: str) -> str:
    """Belirli bir zaman için hatırlatma kurar. due_at ISO-8601 biçiminde UTC olmalıdır (ör. '2026-07-31T15:00:00Z'). Geçmiş tarih verildiğinde veya geçersiz biçimde hata mesajı döner."""
    try:
        return reminders.set_reminder(_memory.db, text, due_at)
    except Exception:
        logging.exception("set_reminder: hatırlatma kurulamadı")
        return "Hata: Hatırlatma şu an kurulamıyor."


def list_reminders() -> dict:
    """Bekleyen tüm hatırlatmaları zaman sırasına göre listeler."""
    try:
        return reminders.list_reminders(_memory.db)
    except Exception:
        logging.exception("list_reminders: hatırlatmalar okunamadı")
        return {"hata": "Hatırlatmalar şu an okunamıyor"}


ALL_TOOLS = [get_user_profile, update_user_profile, remember_fact, add_lesson, search_memory,
             get_speaker_status, watch_repo, unwatch_repo, list_watched_repos, get_repo_updates,
             consult.consult_gemini, consult.consult_claude, check_my_vitals,
             set_reminder, list_reminders]
