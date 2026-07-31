"""Misafir Kapısı (North Star §4.9): JARVIS'in *seçilmiş* araçlarını dış
AI'lara açan, kimlik-doğrulamalı MCP (Model Context Protocol) endpoint'i.

Mimari (kurulu mcp==1.28.1 kaynağından doğrulanmıştır):

- Transport: FastMCP'nin streamable HTTP transport'u
  (``FastMCP.streamable_http_app()``), ``stateless_http=True`` +
  ``json_response=True`` ile. Stateless seçim bilinçli: Cloud Run birden çok
  instance koşturabilir ve misafir çağrıları oturum durumu taşımaz; stateless
  modda her HTTP isteği kendi transport'unu alır (streamable_http_manager.py
  ``_handle_stateless_request``) ve düşük-seviye sunucu ``stateless=True`` ile
  koştuğu için initialize-öncesi istek kontrolü uygulanmaz. ``json_response``
  SSE yerine düz JSON cevabı verir — dış AI client'ları için daha basit.
- Mount: FastMCP app'i ``streamable_http_path="/"`` ile kurulur ve ana FastAPI
  app'ine ``/mcp`` altında mount edilir (main.py). Starlette Mount prefix'i
  soyduğu için FastMCP kendi "/" route'unda çalışır; ``/mcp`` (slash'siz)
  isteği Starlette'in redirect_slashes'ıyla ``/mcp/``'e 307 döner.
- Lifecycle: ``StreamableHTTPSessionManager`` istek işlemeden ÖNCE ``run()``
  context'i aktif olmalı (aksi halde "Task group is not initialized"
  fırlatır — streamable_http_manager.py:159) ve ``run()`` aynı instance'da
  yalnızca BİR KEZ çağrılabilir (streamable_http_manager.py:121). Bu yüzden
  ``start()`` her çağrıda taze bir FastMCP + session manager kurar; ``stop()``
  context'i kapatır. main.py bunları startup/shutdown hook'larına bağlar.
- DNS-rebinding koruması bilinçli KAPALI: FastMCP host "127.0.0.1"
  (varsayılan) olduğunda TransportSecuritySettings'i otomatik açar ve Host
  header'ı localhost olmayan her isteği reddeder. Bu endpoint Cloud Run'da
  PUBLIC çalışır; gerçek koruma aşağıdaki Bearer-token kontrolüdür, Host
  header kontrolü değil.

Araç seti (North Star §4.9 + §9: misafir bölgesi = YEŞİL + SARININ ALT
KÜMESİ, kırmızıya ASLA):

- Bu fazda yalnızca GREEN bölgedeki salt-okuma / düşük-riskli yazma araçları:
  get_user_profile, search_memory, remember_fact, add_lesson,
  list_watched_repos, get_repo_updates. Hepsi config.TOOL_ZONES'ta "green".
- YELLOW yazma araçları (update_user_profile, watch_repo, unwatch_repo) bu
  fazda YOK: misafir kimliği tek bir Google allowlist'ine dayanıyor; sarı
  bölge yazmaları ileride scoped-token (araç-bazlı yetki) ile açılacak.
- get_speaker_status config'de GREEN ama burada ÇALIŞMAZ: imzası ADK'ya
  özgü ``tool_context`` istiyor (voice_trust.lookup + session.user_id);
  MCP isteğinde ADK ToolContext'i yoktur, uydurma bir context üretmek ses
  kimliği durumunu yanlış temsil ederdi. Bu yüzden dışarıda bırakıldı.

Derinlikli savunma: kayıtlı set derleme-zamanı sabiti olsa bile HER çağrı
policy.check_zone'dan geçer — config.TOOL_ZONES ileride bir aracı green'den
indirirse kapı, kayıtlı olmasına rağmen çağrıyı reddeder. Her karar (allow
VEYA block) policy.write_audit ile ana policy callback'in (actor
"orchestrator") kullandığı AYNI entry şemasıyla, actor ``guest:<email>``
olarak audit'e yazılır. Audit, main._init'in kurduğu FirestoreAudit tekili
paylaşılır — ikinci bir Firestore client AÇILMAZ.

Auth: her HTTP isteği ``Authorization: Bearer <google id token>`` taşımak
zorunda. Doğrulama auth.verify_token_email'in ta kendisi (HTTP ve WS
kapılarıyla aynı kod yolu): token'suz → 401, geçersiz token → 401,
allowlist dışı / doğrulanmamış e-posta → 403. Doğrulanan e-posta bir
contextvar'a yazılır; araç çağrısı session manager'ın task group'unda koşsa
bile anyio context kopyaladığı için audit actor'ü doğru e-postayı görür.
FastMCP'nin kendi TokenVerifier'ı bilerek KULLANILMADI: o OAuth 2.1 metadata
uçları ve scope kontrolü içindir; burada gereken, mevcut Google-ID-token +
allowlist semantiğinin (401 vs 403 ayrımı dahil) birebir aynısı.

Araç implementasyonları app/tools.py'dekileri ÇAĞIRIR (iş mantığı kopyası
yok). tools fonksiyonları senkron ve blocking Firestore I/O yapar; FastMCP
async handler'ları içinde ``asyncio.to_thread`` ile çağrılırlar — aksi halde
/api/chat ve /ws/voice'u da sunan aynı event loop donar (main.enroll'daki
gerekçenin aynısı).
"""
import asyncio
import contextvars
import logging
from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse

from . import config, policy, tools
from .auth import verify_token_email

# Doğrulanan misafir e-postası; _GuestAuth yazar, _run_guest_tool audit actor'ü
# için okur. ContextVar çünkü MCP isteği ayrı bir anyio task'ında işlenir.
_guest_email: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "guest_gate_email", default=None
)


def _audit_writer():
    """Ana sürecin audit tekili: main._init() (idempotent, lazy) aynı
    Firestore client'ı kurar; burada İKİNCİ bir client açılmaz."""
    from . import main  # lazy import: main bu modülü import eder (cycle yok)

    main._init()
    return main._audit


async def _run_guest_tool(name: str, fn, kwargs: dict):
    """Zone kontrolü + audit + (thread'de) gerçek araç çağrısı.

    Audit karar ÖNCESİ yazılır (policy_callback deseni); zone green değilse
    araç HİÇ çalışmaz. Her iki yol da blocking Firestore I/O yaptığı için
    tamamı worker thread'de koşar."""
    email = _guest_email.get() or "unknown"
    zone = policy.check_zone(name)
    decision = "allow" if zone == config.ZONE_GREEN else "block"

    def _sync():
        audit = _audit_writer()
        policy.write_audit(
            audit,
            actor=f"guest:{email}",
            tool_name=name,
            args=kwargs,
            zone=zone,
            decision=decision,
        )
        if decision == "block":
            raise ToolError(
                f"POLİTİKA ENGELİ: '{name}' misafir kapısında çalıştırılamaz "
                f"(bölge: {zone}, yalnızca green)."
            )
        return fn(**kwargs)

    try:
        return await asyncio.to_thread(_sync)
    except ToolError:
        raise
    except Exception:
        # Misafire iç hata detayı sızdırma; araç zaten kendi hata sözlüğünü
        # döndürmediyse bu beklenmedik bir çökmedir — logla, generic fırlat.
        logging.exception("guest_gate: tool '%s' beklenmedik hata verdi", name)
        raise ToolError(f"'{name}' şu an çalıştırılamıyor (altyapı hatası).")


# -- Araç sarmalayıcıları: imzalar tools.py'dekilerin birebir aynısı (FastMCP
# input şemasını type hint'lerden üretir), açıklamalar aynı docstring'lerden
# kopyalanır (kayıt anında, aşağıdaki _GUEST_TOOL_SPECS).


async def _tool_get_user_profile() -> dict:
    return await _run_guest_tool("get_user_profile", tools.get_user_profile, {})


async def _tool_search_memory(query: str) -> list[dict]:
    return await _run_guest_tool("search_memory", tools.search_memory, {"query": query})


async def _tool_remember_fact(fact: str) -> str:
    return await _run_guest_tool("remember_fact", tools.remember_fact, {"fact": fact})


async def _tool_add_lesson(context: str, tried: str, went_wrong: str, correct: str) -> str:
    return await _run_guest_tool(
        "add_lesson",
        tools.add_lesson,
        {"context": context, "tried": tried, "went_wrong": went_wrong, "correct": correct},
    )


async def _tool_list_watched_repos() -> dict:
    return await _run_guest_tool("list_watched_repos", tools.list_watched_repos, {})


async def _tool_get_repo_updates() -> dict:
    return await _run_guest_tool("get_repo_updates", tools.get_repo_updates, {})


# Kayıt tablosu: (sarmalayıcı, MCP araç adı, açıklama). Açıklamalar tools.py
# docstring'lerinden gelir — LLM'in gördüğü metin ana agent'la aynı kalır.
_GUEST_TOOL_SPECS = [
    (_tool_get_user_profile, "get_user_profile", tools.get_user_profile.__doc__),
    (_tool_search_memory, "search_memory", tools.search_memory.__doc__),
    (_tool_remember_fact, "remember_fact", tools.remember_fact.__doc__),
    (_tool_add_lesson, "add_lesson", tools.add_lesson.__doc__),
    (_tool_list_watched_repos, "list_watched_repos", tools.list_watched_repos.__doc__),
    (_tool_get_repo_updates, "get_repo_updates", tools.get_repo_updates.__doc__),
]

# Testlerin ve dokümanın başvuracağı kanonik isim listesi.
GUEST_TOOL_NAMES = frozenset(name for _, name, _ in _GUEST_TOOL_SPECS)


def _build_mcp() -> FastMCP:
    """Taze bir FastMCP kur. start() her process-lifecycle'da bir kez çağırır
    (StreamableHTTPSessionManager.run() tek-atış olduğu için yeniden
    kullanılamaz — modül docstring'indeki lifecycle notu)."""
    mcp = FastMCP(
        name="jarvis-guest-gate",
        instructions=(
            "JARVIS Misafir Kapısı: Kadir'in kişisel asistanının seçilmiş, "
            "salt-green araçları. Her çağrı policy + audit katmanından geçer."
        ),
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    for fn, name, description in _GUEST_TOOL_SPECS:
        mcp.add_tool(fn, name=name, description=description)
    return mcp


class _GuestAuth:
    """ASGI middleware: Bearer Google ID token zorunluluğu.

    FastAPI dependency'si değil, saf ASGI: FastMCP app'i Starlette olarak
    mount edildiği için FastAPI'nin dependency zincirine giremez. Semantik
    auth.require_user ile birebir aynı (401 vs 403 ayrımı dahil).
    verify_token_email Google'ın JWKS uçlarına giden blocking I/O yapar —
    thread'de çalışır."""

    def __init__(self, app):
        self._app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])  # ASGI: key'ler lowercase bytes
        authorization = headers.get(b"authorization", b"").decode("latin1")
        if not authorization.startswith("Bearer "):
            await self._reject(scope, receive, send, 401, "Giriş gerekli")
            return
        try:
            email = await asyncio.to_thread(
                verify_token_email, authorization.removeprefix("Bearer ")
            )
        except PermissionError as exc:
            status = 401 if str(exc) == "Geçersiz oturum" else 403
            await self._reject(scope, receive, send, status, str(exc))
            return
        token = _guest_email.set(email)
        try:
            await self._app(scope, receive, send)
        finally:
            _guest_email.reset(token)

    @staticmethod
    async def _reject(scope, receive, send, status: int, detail: str):
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


@dataclass
class _Active:
    """Çalışan kapı instance'ı: FastMCP + auth-sarmalı ASGI app + run context'i."""

    mcp: FastMCP
    app: _GuestAuth
    run_cm: object  # StreamableHTTPSessionManager.run() async context manager'ı


_active: _Active | None = None


async def start() -> None:
    """Kapıyı aç (idempotent). main.py'nin startup hook'u çağırır.

    Session manager'ın run() context'i girilmeden TEK bir istek bile
    işlenemez (streamable_http_manager.py:159-160); run() aynı manager'da bir
    kez çağrılabildiği için her start taze FastMCP kurar — bu, testlerin
    ardışık TestClient lifespan'larında da güvenle çalışmasını sağlar."""
    global _active
    if _active is not None:
        return
    mcp = _build_mcp()
    # streamable_http_app() ÖNCE: session_manager property'si, manager'ı lazy
    # yaratan bu çağrı yapılmadan RuntimeError fırlatır (fastmcp/server.py).
    app = _GuestAuth(mcp.streamable_http_app())
    run_cm = mcp.session_manager.run()
    await run_cm.__aenter__()
    _active = _Active(mcp=mcp, app=app, run_cm=run_cm)


async def stop() -> None:
    """Kapıyı kapat (idempotent). main.py'nin shutdown hook'u çağırır."""
    global _active
    if _active is None:
        return
    active, _active = _active, None
    await active.run_cm.__aexit__(None, None, None)


async def asgi(scope, receive, send) -> None:
    """main.py'nin /mcp altına mount ettiği ASGI uç noktası."""
    active = _active
    if active is None:
        await JSONResponse(
            {"detail": "Misafir kapısı henüz başlatılmadı"}, status_code=503
        )(scope, receive, send)
        return
    await active.app(scope, receive, send)
