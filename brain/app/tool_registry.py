"""Araç kayıt defteri (North Star §8.5, Faz Y4.1): kazanılmış yetenekler KOD
değil VERİdir.

Y4 öncesinde Jarvis'in araç kümesi derleme anında sabitti: `tools.ALL_TOOLS`
bir Python listesi, `config.TOOL_ZONES` bir sözlük. Yeni bir yetenek kazanmanın
tek yolu bir insanın kod yazıp deploy etmesiydi. Bu modül o kümenin veri tarafını
kurar: onaylanmış her yetenek `tool_registry` koleksiyonunda bir dokümandır.

Model (Firestore, doküman kimliği = araç/sunucu ADI — tekil isim uzayı):
{name, kind: builtin|mcp, zone: green|yellow, status: granted|revoked, why,
 mcp: dict|None, approval_id, granted_at, revoked_at}

Dört değişmez bu modülün nasıl yazıldığını belirler:

1. **Kayıt yazan tek yol onay yürütücüsüdür** (spec §4.1). Bu modül `grant()`
   fonksiyonunu SAĞLAR ama kimseyi çağırmaz; üretimde tek çağıran
   `tools._execute_tool_grant` — yani `approvals.decide(..., "approved")`.
   `propose_tool` buraya YAZMAZ.
2. **Kayıt defteri koddaki bölgeyi GEVŞETEMEZ** (spec §4.2). Bu garanti burada
   değil `policy.check_zone`'da uygulanır (sıra: config.TOOL_ZONES -> kayıt
   defteri -> DEFAULT_ZONE) — ama sebebi burada yazılı: aksi hâlde onaylanmış
   tek bir `{name: "cancel_reminder", zone: "green"}` kaydı onay merkezini
   tamamen baypas ederdi.
3. **Kırmızı asla verilmez** (spec §4.3). `grant()` de doğrular, `propose_tool`
   da: öneri doğrulaması tek savunma hattı değildir.
4. **Revoke geri alınabilirliktir** (spec §4.4): kayıt SİLİNMEZ, `revoked`
   damgalanır — ve aynı ad yeniden kazandırılabilir (`grant()`'in AlreadyExists
   dalı bu yüzden koşulsuz "hayır" demez).

Fonksiyonlar exception FIRLATMAZ, Türkçe gözlem döner (İlke 4): bu modülün
çağıranı ya bir araç gövdesi ya da bir onay yürütücüsüdür; ikisi de bir
hatayı kullanıcıya metin olarak taşır.
"""
import logging
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists

from . import config

COLLECTION = "tool_registry"

KIND_BUILTIN = "builtin"
KIND_MCP = "mcp"
KINDS = (KIND_BUILTIN, KIND_MCP)

STATUS_GRANTED = "granted"
STATUS_REVOKED = "revoked"

# §4.3: önerilebilir/kazandırılabilir bölgeler. Kırmızı bir yetenek hâlâ insan
# eliyle koda girer — üretilmiş yetenek misafir muamelesi görür.
GRANTABLE_ZONES = (config.ZONE_GREEN, config.ZONE_YELLOW)

# `mcp` sözlüğünün `transport` değerleri (spec §3). `tools._mcp_spec` bugün
# yalnız "http" ve "stdio" üretir; "sse" elle yazılan kayıtlar için desteklenir.
TRANSPORT_STDIO = "stdio"
TRANSPORT_SSE = "sse"
TRANSPORT_HTTP = "http"
TRANSPORT_STREAMABLE_HTTP = "streamable_http"

# ADK'nın toolset ön-ekini araç adına EKLEME şekli — google-adk 1.36.2,
# BaseToolset.get_tools_with_prefix: `prefixed_name = f"{prefix}_{tool.name}"`.
# Bölge eşlemesi (§6) bu ayıraca dayanır; ADK bunu değiştirirse eşleme kopar ve
# tüm MCP araçları kırmızıya düşer (fail-closed, ama sessizce işlevsiz).
PREFIX_SEPARATOR = "_"

# Ön-ek çakışmasında hangi bölge kazanır: BÜYÜK olan (daha kısıtlayıcı).
# Sözlükte olmayan (bozuk/uydurma) bir değer en kısıtlayıcı sayılır — check_zone
# onu zaten red'e çevirir.
_ZONE_RANK = {config.ZONE_GREEN: 0, config.ZONE_YELLOW: 1, config.ZONE_RED: 2}
_ZONE_RANK_UNKNOWN = 99


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate(name: str, kind: str, zone: str) -> str | None:
    """Ortak doğrulama: sorun varsa Türkçe gözlem, yoksa None.

    `propose_tool` (öneri anı) ve `grant()` (kazandırma anı) AYNI kuralları
    uygular. İki kez doğrulamak fazlalık değil: öneriyle kazandırma arasında
    onay kartı ve Firestore turu var, ve kayıt defterine yazan yol kendi
    girdisini kendisi doğrulamak zorundadır."""
    if not isinstance(name, str) or not name.strip():
        return "Araç adı boş olamaz."
    if kind not in KINDS:
        return f"Geçersiz araç türü: '{kind}' (beklenen: {' veya '.join(KINDS)})."
    if zone == config.ZONE_RED:
        return ("Kırmızı bölge önerilemez: kazanılan bir yetenek en fazla sarı "
                "olabilir (§8.5). Kırmızı bir yetenek koda insan eliyle girer.")
    if zone not in GRANTABLE_ZONES:
        return f"Geçersiz bölge: '{zone}' (beklenen: {' veya '.join(GRANTABLE_ZONES)})."
    return None


def grant(db, *, name: str, kind: str, zone: str, why: str, approval_id: str,
          mcp: dict | None = None, now_fn=_now) -> str:
    """Bir yeteneği kayıt defterine yazar. ÜRETİMDE TEK ÇAĞIRAN onay
    yürütücüsüdür (spec §4.1) — bu fonksiyonun bir araç gövdesinden
    çağrılması, insan yetkilendirmesini baypas etmek demektir.

    Yazım `create()` ile başlar: Firestore'un atomik "yoksa yaz"ı (aynı desen
    approvals.py'nin claim'i ve conversations.py'nin başlık yarışı). Çakışma
    hâlinde ne yapılacağı MEVCUT kaydın durumuna bağlıdır:
    - `granted` -> DOKUNULMAZ. İkinci bir öneri var olan bir yeteneğin bölgesini
      veya gerekçesini sessizce ezemez.
    - `revoked` -> yeniden kazandırılır (§4.4). Aksi hâlde bir kez iptal edilen
      ad sonsuza dek yakılmış olurdu.
    """
    problem = validate(name, kind, zone)
    if problem:
        logging.warning("tool_registry: grant reddedildi name=%r kind=%r zone=%r -- %s",
                        name, kind, zone, problem)
        return problem

    name = name.strip()
    now = now_fn()
    doc = {
        "name": name,
        "kind": kind,
        "zone": zone,
        "status": STATUS_GRANTED,
        "why": why,
        "mcp": mcp if kind == KIND_MCP else None,
        "approval_id": approval_id,
        "granted_at": now,
        "revoked_at": None,
    }
    ref = db.collection(COLLECTION).document(name)
    try:
        ref.create(doc)
    except AlreadyExists:
        current = ref.get().to_dict() or {}
        if current.get("status") == STATUS_GRANTED:
            logging.info("tool_registry: '%s' zaten kayıtlı -- üzerine yazılmadı", name)
            return f"'{name}' zaten araç kayıt defterinde kayıtlı; kayıt değiştirilmedi."
        # merge=True: dokümanın bilinmeyen/ileride eklenen alanları korunur;
        # revoked_at açıkça temizlenir, yoksa granted bir kayıt iptal damgası
        # taşımaya devam ederdi.
        ref.set(doc, merge=True)
        logging.info("tool_registry: '%s' yeniden kazandırıldı (önceki durum: %s)",
                     name, current.get("status"))

    logging.info("tool_registry: grant name=%s kind=%s zone=%s approval=%s",
                 name, kind, zone, approval_id)
    return (f"'{name}' araç kayıt defterine eklendi (bölge: {zone}). "
            "Bir sonraki açılışta etkin olacak.")


def revoke(db, name: str, *, now_fn=_now) -> str:
    """Bir yeteneği geri alır: kayıt SİLİNMEZ, `revoked` damgalanır (§4.4).

    Silmemek iki şey satın alır: audit izi (§8.5 değişmez 5) okunabilir kalır ve
    hangi yeteneğin ne zaman verilip ne zaman alındığı geri kurulabilir."""
    ref = db.collection(COLLECTION).document(name)
    snap = ref.get()
    if not snap.exists:
        return f"'{name}' araç kayıt defterinde yok."
    ref.set({"status": STATUS_REVOKED, "revoked_at": now_fn()}, merge=True)
    logging.info("tool_registry: revoke name=%s", name)
    return f"'{name}' geri alındı; kayıt iptal edilmiş olarak duruyor."


def get(db, name: str) -> dict | None:
    """Tek kaydın ham hâli (durum farkı gözetmeksizin) veya None."""
    snap = db.collection(COLLECTION).document(name).get()
    return snap.to_dict() if snap.exists else None


def list_granted(db) -> list[dict]:
    """Yalnızca `granted` kayıtlar, ada göre sıralı."""
    rows = [snap.to_dict() for snap in db.collection(COLLECTION).stream()]
    granted = [r for r in rows if r.get("status") == STATUS_GRANTED]
    # str() -- modül kayıt-BAŞINA izolasyon vaat ediyor, ama izolasyon
    # toolset_factory çağrısını sarıyordu, bu listeleme adımını değil:
    # `name` alanı string olmayan tek bir doküman sort'u TypeError ile
    # düşürüyor ve SAĞLAM sunucuların hepsi birlikte gidiyordu.
    granted.sort(key=lambda r: str(r.get("name") or ""))
    return granted


def _zone_from_mcp_prefix(db, tool_name: str) -> str | None:
    """Bir MCP sunucusundan gelen aracın bölgesi (spec §6).

    Bir MCP sunucusunun araç adları önceden BİLİNMEZ, bu yüzden kaydın zone'u o
    sunucunun TÜM araçlarına uygulanır. Eşleme mekanizması ADK'nın kendi ön-ek
    üretimidir: `mcp_toolsets` her toolset'i `tool_name_prefix=<kayıt adı>` ile
    kurar ve ADK araç adını `f"{prefix}_{ad}"` yapar (kaynaktan doğrulandı,
    PREFIX_SEPARATOR'a bakınız). Yani `github_mcp` kaydı `github_mcp_list_prs`'i
    kapsar — ama `github_mcpanything`'i KAPSAMAZ (ayıraç zorunlu).

    Yalnız `kind="mcp"` kayıtlar ön-ek ödünç verir: builtin bir kayıt tek bir
    aracın adıdır, bir isim uzayı değil.

    İki kayıt aynı adı ön-ekliyorsa hangisinden geldiği bilinemez ('github' +
    'mcp_list' mi, 'github_mcp' + 'list' mi?) — o yüzden EN KISITLAYICI bölge
    kazanır. Hiç eşleşme yoksa None: çağıran DEFAULT_ZONE'a (red) düşer, spec
    §6'nın "bilinmiyorsa güvenli taraf" kuralı."""
    best: str | None = None
    best_rank = -1
    for record in list_granted(db):
        if record.get("kind") != KIND_MCP:
            continue
        server = (record.get("name") or "").strip()
        if not server or not tool_name.startswith(server + PREFIX_SEPARATOR):
            continue
        zone = record.get("zone")
        rank = _ZONE_RANK.get(zone, _ZONE_RANK_UNKNOWN)
        if rank > best_rank:
            best, best_rank = zone, rank
    return best


def zone_for(db, name: str) -> str | None:
    """Kayıt defterinin bu ad için önerdiği bölge; eşleşme yoksa None.

    İki eşleşme yolu, bu sırayla:
    1. TAM AD — Kadir'in o adı açıkça onayladığı kayıt (builtin veya MCP sunucu
       adının kendisi). En özgül ifade, o yüzden önce gelir.
    2. MCP ÖN-EKİ — `_zone_from_mcp_prefix`: onaylanmış bir MCP sunucusundan
       gelen, adı derleme anında bilinemeyen araçlar (spec §6).

    HİÇBİR hata dışarı sızmaz: Firestore erişilemezse None döner, çağıran
    (policy.check_zone) da DEFAULT_ZONE'a (red) düşer. Fail-closed — bir altyapı
    hıçkırığı bir aracı ASLA açmaz, ama ajanı da düşürmez."""
    try:
        doc = get(db, name)
        if doc and doc.get("status") == STATUS_GRANTED:
            return doc.get("zone")
        return _zone_from_mcp_prefix(db, name)
    except Exception:
        logging.exception("tool_registry: zone_for okunamadı name=%s -- red'e düşülüyor", name)
        return None


def make_zone_resolver(db):
    """`policy.check_zone`'a enjekte edilen çözücü: (araç adı) -> bölge | None.

    `check_zone` bu yüzden db'siz ve saf kalır; kayıt defterine bağımlılık
    çağrı anında ENJEKTE edilir (üretimde main._init, testlerde doğrudan).
    Çözücü verilmeyen her çağrı — guest_gate dahil — Y4 öncesiyle birebir aynı
    davranır."""
    return lambda name: zone_for(db, name)


# ---------------------------------------------------------------------------
# MCP eklentisi (spec §6): onaylı sunucular ajana GERÇEKTEN bağlanır
# ---------------------------------------------------------------------------


def build_mcp_toolset(record: dict):
    """Tek bir `kind="mcp"` kaydından ADK toolset nesnesi kurar.

    **Kurulu google-adk 1.36.2'den KAYNAK OKUNARAK doğrulanan imzalar**
    (`.venv/.../google/adk/tools/mcp_tool/`); `MCPToolset.__init__` dekoratörlü
    olduğu için `inspect.signature` yanıltıcıdır, aşağıdakiler dosyadan:

        # mcp_toolset.py — NOT: `MCPToolset` DEPRECATED (DeprecationWarning
        # fırlatır), doğru ad `McpToolset`. Tüm parametreler KEYWORD-ONLY.
        McpToolset.__init__(self, *, connection_params, tool_filter=None,
            tool_name_prefix=None, errlog=sys.stderr, auth_scheme=None,
            auth_credential=None, require_confirmation=False,
            header_provider=None, progress_callback=None,
            use_mcp_resources=False, sampling_callback=None,
            sampling_capabilities=None, credential_key=None)

        # mcp_session_manager.py — üçü de pydantic BaseModel
        StdioConnectionParams(server_params: StdioServerParameters,
                              timeout: float = 5.0)
        SseConnectionParams(url: str, headers=None, timeout: float = 5.0,
                            sse_read_timeout: float = 300.0,
                            httpx_client_factory=create_mcp_http_client)
        StreamableHTTPConnectionParams(url: str, headers=None,
                            timeout: float = 5.0, sse_read_timeout: float = 300.0,
                            terminate_on_close: bool = True,
                            httpx_client_factory=create_mcp_http_client)

        # mcp (SDK 1.28.1)
        StdioServerParameters(command: str, args: list[str] = [], env=None,
                              cwd=None, encoding="utf-8",
                              encoding_error_handler="strict")

    Bu çağrı AĞ AÇMAZ ve SÜREÇ BAŞLATMAZ: `McpToolset.__init__` yalnızca bir
    `MCPSessionManager` kurar; bağlantı ilk `get_tools()`'ta `create_session()`
    ile açılır (kaynaktan doğrulandı). Bu yüzden soğuk başlangıç, ölü bir MCP
    sunucusu yüzünden yavaşlamaz — hata `get_tools()` anında çıkar ve ADK'nın
    kendi `_convert_tool_union_to_tools`'u onu loglayıp boş liste döndürür.

    `tool_name_prefix` kaydın adıdır ve bu SÜSLEME DEĞİL, bölge eşlemesinin
    taşıyıcısıdır: `_zone_from_mcp_prefix` gelen araçları bu ön-ekten tanır.

    Taşıma seçilemezse FIRLATIR — çağıran `mcp_toolsets` bunu yakalayıp kaydı
    atlar, yani bozuk bir taşıma tanımı sessizce YANLIŞ bir toolset kurmaz."""
    from mcp import StdioServerParameters

    from google.adk.tools.mcp_tool import (McpToolset, SseConnectionParams,
                                           StdioConnectionParams,
                                           StreamableHTTPConnectionParams)

    name = (record.get("name") or "").strip()
    if not name:
        raise ValueError("MCP kaydının adı yok; araç ön-eki kurulamaz.")

    spec = record.get("mcp") or {}
    transport = str(spec.get("transport") or "").strip().lower()
    url = str(spec.get("url") or "").strip()
    command = str(spec.get("command") or "").strip()
    args = [str(a) for a in (spec.get("args") or [])]

    # Taşıma yazılmamışsa alanlardan çıkarılır: elle yazılmış veya eski bir
    # kayıt `transport` taşımayabilir.
    if not transport:
        transport = TRANSPORT_STDIO if command else (TRANSPORT_HTTP if url else "")

    if transport == TRANSPORT_STDIO:
        if not command:
            raise ValueError(f"'{name}': stdio taşıması için `command` gerekli.")
        params = StdioConnectionParams(
            server_params=StdioServerParameters(command=command, args=args))
        target = " ".join([command, *args])
    elif transport == TRANSPORT_SSE:
        if not url:
            raise ValueError(f"'{name}': sse taşıması için `url` gerekli.")
        params = SseConnectionParams(url=url)
        target = url
    elif transport in (TRANSPORT_HTTP, TRANSPORT_STREAMABLE_HTTP):
        if not url:
            raise ValueError(f"'{name}': {transport} taşıması için `url` gerekli.")
        params = StreamableHTTPConnectionParams(url=url)
        target = url
    else:
        raise ValueError(f"'{name}': bilinmeyen MCP taşıması {transport!r}.")

    logging.info("tool_registry: MCP toolset kuruluyor name=%s transport=%s hedef=%s",
                 name, transport, target)
    return McpToolset(connection_params=params, tool_name_prefix=name)


def mcp_toolsets(db, *, toolset_factory=None) -> list:
    """`status="granted"` + `kind="mcp"` kayıtlardan ADK toolset listesi
    (spec §6). `agent.build_agent(..., extra_toolsets=...)` bunları
    `ALL_TOOLS`'un yanına ekler; üretimde çağıran main._init/_init_voice.

    **HATA İZOLASYONU (spec §6, taşıyıcı garanti):** bir kaydın toolset kurulumu
    FIRLARSA o kayıt atlanır, diğerleri kurulur ve bu fonksiyon FIRLATMAZ. Tek
    bozuk bir kayıt (yanlış URL, tanınmayan taşıma, hatta MCP paketinin hiç
    kurulmamış olması) Jarvis'i komple susturmamalıdır: izolasyon olmasaydı
    build_agent fırlatır, runner kurulmaz, HİÇBİR araç çalışmazdı.

    Kayıt defterinin kendisi okunamazsa da aynı karar: boş liste, MCP'siz ama
    ÇALIŞAN bir ajan.

    `toolset_factory` testler içindir: gerçek `build_mcp_toolset` yerine
    enjekte edilebilir, böylece testler ağ/süreç açan bir MCP sunucusuna
    ihtiyaç duymaz.

    NOT (§4.5): burası süreç ömrüne bağlıdır — çalışan bir ADK ajanının araç
    listesi yerinde değiştirilmez. Onay kartı da bunu söyler: "bir sonraki
    açılışta etkin olacak"."""
    factory = toolset_factory or build_mcp_toolset
    try:
        records = list_granted(db)
    except Exception:
        logging.exception(
            "tool_registry: kayıt defteri okunamadı -- ajan MCP sunucusu OLMADAN kuruluyor")
        return []

    toolsets, skipped = [], 0
    for record in records:
        if record.get("kind") != KIND_MCP:
            continue
        name = record.get("name")
        try:
            toolset = factory(record)
        except Exception:
            skipped += 1
            logging.exception(
                "tool_registry: MCP toolset kurulamadı name=%s mcp=%r -- bu kayıt "
                "ATLANIYOR, diğer sunucular bağlanmaya devam ediyor",
                name, record.get("mcp"))
            continue
        if toolset is None:
            skipped += 1
            logging.warning(
                "tool_registry: MCP fabrikası None döndü name=%s -- atlanıyor", name)
            continue
        toolsets.append(toolset)
        logging.info("tool_registry: MCP toolset bağlandı name=%s zone=%s",
                     name, record.get("zone"))

    logging.info("tool_registry: MCP bağlama özeti kurulan=%d atlanan=%d",
                 len(toolsets), skipped)
    return toolsets
