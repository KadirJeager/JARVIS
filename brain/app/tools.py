"""Agent-facing tool functions. Docstrings are the LLM's tool descriptions (Turkish)."""
import logging
import re
import shlex
import uuid

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1.base_query import FieldFilter

from . import (approvals, consult, reminders, repo_watch, speaker_history,
               speaker_store, tool_registry, vitals, voice_trust)
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


def cancel_reminder(reminder_id: str) -> str:
    """Bekleyen bir hatırlatmayı iptal eder. reminder_id, list_reminders'ın döndürdüğü ID'dir. KIRMIZI bölge: bu araç bir kayıt siler, bu yüzden Kadir'in ONAYINI gerektirir — çağırdığında bir onay kartı oluşur ve iptal ancak Kadir onayladıktan sonra gerçekleşir. Gönderilmiş bir hatırlatma iptal edilemez."""
    # Bu gövde SAVUNMA amaçlıdır, üretim yolu değildir: kırmızı bölge
    # before_tool_callback'i (app/policy.py) çağrıyı buraya varmadan keser ve
    # yerine bir onay kartı oluşturur. Gövdeye ulaşılıyorsa politika bağlantısı
    # kopmuş demektir — o hâlde araç hatırlatmaya DOKUNMAZ (fail-closed) ve
    # durumu gözlem olarak bildirir. Gerçek iptal approvals.decide() içinden,
    # aşağıdaki yürütücü üzerinden çalışır.
    logging.warning("cancel_reminder: politika kesmeden gövdeye ulaşıldı id=%r — iptal YAPILMADI",
                    reminder_id)
    return "Bu araç yalnızca Kadir'in onayından sonra çalışır."


def _execute_cancel_reminder(tool_args: dict, user_id: str) -> str:
    """`cancel_reminder`'ın onay-sonrası yürütücüsü: (tool_args, user_id) -> metin.

    db, çağrı anında modül tekili `_memory.db`'den okunur — bu dosyadaki her
    araç gövdesinin zaten kullandığı desen. Alternatif (yürütücüye db enjekte
    etmek) kayıt defterini bir fabrikaya çevirir ve `main._init()` sırasına bağlı
    bir kurulum adımı daha ekler; `_memory` ise `tools.init(memory)` ile zaten
    kurulmuş oluyor. `user_id` şu an kullanılmıyor: `reminders` koleksiyonu tek
    kullanıcılıdır ([[kapsam-tek-kullanici]]); sahiplik sınırı bir üst katmanda,
    approvals.decide()'ın sahiplik kontrolündedir.
    """
    return reminders.cancel(_memory.db, str((tool_args or {}).get("reminder_id", "")))


approvals.register_executor("cancel_reminder", _execute_cancel_reminder)


# -- Araç kazanım merdiveni (North Star §8.5, Faz Y4.1) ----------------------
#
# "Keşif ve kurulum otonomdur; yetkilendirme her zaman insanidir." Bu iki
# fonksiyon o cümlenin iki yarısıdır ve bilinçli olarak AYRIKtır:
# `propose_tool` yalnızca bir onay kurar, `_execute_tool_grant` yalnızca onay
# sonrası çalışır. Kayıt defterine yazan TEK yer aşağıdaki yürütücüdür.

PROPOSAL_QUEUED = (
    "ÖNERİ ONAYA GÖNDERİLDİ: '{name}' ({zone} bölge) için Kadir'e onay kartı "
    "oluşturuldu. Kadir'e öneriyi ve gerekçeni söyle, sonra BEKLE — aynı araç "
    "için ikinci bir kart oluşturma, kararı kart üzerinden verir."
)


def _mcp_spec(kind: str, mcp_url: str, mcp_command: str, scopes: str) -> dict | None:
    """Kayıt defterine yazılacak `mcp` alanı (spec §3) — düz string'lerden.

    Onay dokümanındaki `tool_args` değerleri `approvals.request` tarafından
    stringify edilir (audit ile aynı 500 karakter kuralı), bu yüzden öneri
    alanları düz string taşınır ve yapıya ANCAK burada, kayıt anında çevrilir.
    Komut satırı shlex ile ayrılır: `command` + `args` ayrımı MCP stdio
    ulaşımının istediği şekildir ve tırnaklı argümanları doğru korur."""
    if kind != tool_registry.KIND_MCP:
        return None
    if mcp_url:
        return {"transport": "http", "url": mcp_url, "command": None, "args": [],
                "scopes": _scope_list(scopes)}
    parts = shlex.split(mcp_command)
    return {"transport": "stdio", "url": None, "command": parts[0] if parts else "",
            "args": parts[1:], "scopes": _scope_list(scopes)}


def _scope_list(scopes: str) -> list[str]:
    """Virgülle ayrılmış scope metnini listeye çevirir. Bu alan bu dilimde
    BELGELEYİCİDİR (spec §7): karta yazılır, zorlanmaz — OAuth scope zorlaması
    MCP sunucusunun kendi işidir."""
    return [s.strip() for s in (scopes or "").split(",") if s.strip()]


def propose_tool(name: str, kind: str, zone: str, why: str, tool_context,
                 mcp_url: str = "", mcp_command: str = "", scopes: str = "") -> str:
    """Bir görev için EKSİK olan bir yeteneği Kadir'in onayına önerir. Kendi
    başına hiçbir şey kurmaz: yalnızca bir onay kartı oluşturur, kararı Kadir
    verir. `name` aracın/MCP sunucusunun adı; `kind` "mcp" (dış MCP sunucusu)
    veya "builtin"; `zone` "green" (salt okuma / zararsız) veya "yellow" (yap ve
    bildir) — KIRMIZI istenemez; `why` Kadir'in kartta okuyacağı Türkçe gerekçe
    ("GitHub PR'larını okuyabilmem için gerekiyor" gibi). MCP için `mcp_url`
    (HTTP sunucusu) VEYA `mcp_command` (yerel komut) zorunlu; `scopes` virgülle
    ayrılmış izin listesidir. Onaydan sonra araç bir sonraki açılışta etkin
    olur. Bir aracı önerdikten sonra BEKLE, kendin kurmaya çalışma."""
    try:
        problem = tool_registry.validate(name, kind, zone)
        if problem:
            return problem
        name = name.strip()
        if kind == tool_registry.KIND_MCP and not (mcp_url or mcp_command):
            return ("MCP önerisi için bir ulaşım gerekli: mcp_url (HTTP sunucusu) "
                    "veya mcp_command (yerel komut) ver.")

        db = _memory.db
        existing = tool_registry.get(db, name)
        if existing and existing.get("status") == tool_registry.STATUS_GRANTED:
            return (f"'{name}' zaten araç kayıt defterinde kayıtlı "
                    f"(bölge: {existing.get('zone')}); yeni öneri gerekmiyor.")

        session = tool_context.session
        user_id, session_id = session.user_id, session.id
        # Süresi geçmiş kartlar list_pending'de GÖRÜNMEZ (Y3 §4.1) -- yani
        # cevapsız kalıp süresi dolan bir öneri, aynı aracın yeniden
        # önerilmesini sonsuza dek kilitlemez.
        for pending in approvals.list_pending(db, user_id):
            if pending.get("kind") == approvals.KIND_TOOL_GRANT and pending.get("tool_name") == name:
                return (f"'{name}' için zaten Kadir'in kararını bekleyen bir öneri var; "
                        "ikinci kart oluşturmadım.")

        # Onay id'si ÖNCEDEN üretilir ve tool_args'a konur: yürütücü sözleşmesi
        # (tool_args, user_id) olduğu için kaydı doğuran onayın kimliği
        # (spec §3, izlenebilirlik) yürütücüye ancak böyle ulaşır.
        approval_id = uuid.uuid4().hex
        ulasim = mcp_url or mcp_command or "-"
        approvals.request(
            db,
            user_id=user_id,
            kind=approvals.KIND_TOOL_GRANT,
            title=f"'{name}' yeteneğini kazanayım mı?",
            detail=(f"Jarvis yeni bir yetenek istiyor: {name} ({kind}, {zone} bölge).\n"
                    f"Gerekçe: {why}\n"
                    f"Ulaşım: {ulasim}\n"
                    f"İzinler: {scopes or '-'}\n"
                    "Onaylarsan araç kayıt defterine girer ve bir sonraki açılışta etkin olur."),
            tool_name=name,
            tool_args={"name": name, "kind": kind, "zone": zone, "why": why,
                       "mcp_url": mcp_url, "mcp_command": mcp_command, "scopes": scopes,
                       "approval_id": approval_id},
            zone=zone,
            session_id=session_id,
            doc_id=approval_id,
        )
        logging.info("propose_tool: öneri onaya düştü name=%s kind=%s zone=%s id=%s",
                     name, kind, zone, approval_id)
        return PROPOSAL_QUEUED.format(name=name, zone=zone)
    except Exception:
        logging.exception("propose_tool: öneri oluşturulamadı name=%r", name)
        return "Bu öneri şu an oluşturulamıyor; araç kazanım yolu geçici olarak kapalı."


def _execute_tool_grant(tool_args: dict, user_id: str) -> str:
    """`kind=tool_grant` onaylarının yürütücüsü (spec §4.1).

    KAYIT DEFTERİNE YAZAN TEK YERDİR. `approvals.decide(..., "approved")`
    dışında hiçbir yol buraya varmaz: `propose_tool` yalnızca onay kurar,
    `tool_registry.grant` de başka hiçbir yerden çağrılmaz. `user_id`
    kullanılmıyor — kayıt defteri tek kullanıcılıdır ([[kapsam-tek-kullanici]]),
    sahiplik sınırı bir üst katmanda, decide()'ın sahiplik kontrolündedir
    (`_execute_cancel_reminder` ile aynı gerekçe)."""
    args = tool_args or {}
    kind = str(args.get("kind", ""))
    return tool_registry.grant(
        _memory.db,
        name=str(args.get("name", "")),
        kind=kind,
        zone=str(args.get("zone", "")),
        why=str(args.get("why", "")),
        approval_id=str(args.get("approval_id", "")),
        mcp=_mcp_spec(kind, str(args.get("mcp_url", "")), str(args.get("mcp_command", "")),
                      str(args.get("scopes", ""))),
    )


approvals.register_executor(approvals.EXECUTOR_TOOL_GRANT, _execute_tool_grant)


ALL_TOOLS = [get_user_profile, update_user_profile, remember_fact, add_lesson, search_memory,
             get_speaker_status, watch_repo, unwatch_repo, list_watched_repos, get_repo_updates,
             consult.consult_gemini, consult.consult_claude, check_my_vitals,
             set_reminder, list_reminders, cancel_reminder, propose_tool]
