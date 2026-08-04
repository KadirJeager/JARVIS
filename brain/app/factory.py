"""Ajan Fabrikası — Kademe 1, "Kalıphane" (North Star §8.5, Faz Y4.2).

Varlık sebebi ikame ufkudur (§1): Kadir'in yaptığı her iş türüne derleme anında
statik bir ajan atanamaz. Statik çekirdek (Kademe 0) en sık işleri taşır;
kuyruğun sonsuz çeşitliliğini, şablondan parametreyle örneklenen GEÇİCİ ajanlar
(Kademe 1) karşılar.

Kademe 1'in tanımı: **şablon sayısı derleme anında sabittir** (`TEMPLATES`),
örnek çalışma anında üretilir (araç alt kümesi + görev + bütçe + TTL). Yeni
şablon eklemek bir KOD değişikliğidir; debug yüzeyi bu yüzden statik mimariye
yakın kalır — sonsuz sayıda ajan değil, sonlu sayıda kalıp vardır.

Fabrika anayasası (§8.5 değişmezleri) — hepsi tests/test_factory.py'de pinli:

1. **Misafir muamelesi.** Türetilmiş ajan yeşil + sarı ile başlar, kırmızıya
   asla. Somut sonucu `build_specialist`tedir: politika callback'ine
   `approval_sink` VERİLMEZ (kırmızı çağrı onay kartına bile dönüşmez, düz
   engellenir) ve `zone_resolver` de VERİLMEZ (çözücü yalnızca gevşetebilir —
   misafir yüzeyi kayıt defteriyle genişlemez).
2. **Yalnızca mevcut araçlar.** Küme, şablonun izin verdiği ADLARIN
   `tools.ALL_TOOLS` ile KESİŞİMİdir. Bulunamayan ad sessizce atlanmaz,
   LOGLANIR.
3. **Ajan üretemez** (recursion yasağı). `spawn_specialist`, şablon onu açıkça
   istese bile araç kümesine giremez — bu bir filtre değil, mutlak dışlamadır.
4. **TTL'lidir.** Örnek tek bir çağrının ömrü kadar yaşar ve İKİ BAĞIMSIZ
   tavanla sınırlıdır: adım sayısı ve duvar saati. Biri diğerinin yerine
   geçmez — tek bir araç çağrısı dakikalarca sürebilir (adım tavanı onu
   yakalamaz), hızlı bir döngü saniyede yüzlerce adım atabilir (süre tavanı onu
   yakalamaz).
5. **Bütçe ve iz zorunludur.** Her araç çağrısı audit'e
   `actor="factory:<şablon>#<örnek>"` ile yazılır.
6. **Retro envanteri tarar.** Haftalık retro (§8.4) `audit_log`'u zaten okur;
   `factory:` ön ekli aktörler orada görünür.

**Neden Runner enjekte edilebilir?** Örnek, Kademe 0 ile aynı ADK makinesinde
koşar (aynı model fabrikası, aynı politika katmanı, ayrı bir `Runner`). Ama bir
`Runner` koşturmak GERÇEK bir LLM çağrısıdır: testte koşarsa süit ağa,
kotaya ve modelin gününe bağlanır — ve iki tavanın davranışı (bu dilimin
güvenlik çekirdeği) ölçülemez hâle gelir. Bu yüzden `spawn` `runner_factory` ve
`clock` alır; üretim varsayılanları modülün kendi içindedir, yani main'deki
gerçek kurulum hiç değişmez ve enjeksiyon üretimde hiçbir dala girmez.
"""
import asyncio
import logging
import time
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from typing import Callable

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from . import policy

# Fabrika oturumları KENDİ uygulama adında yaşar. Kadir'in sohbeti main.APP_NAME
# ("jarvis") altındadır; ayrı ad + ayrı oturum servisi + ayrı oturum kimliği,
# "sohbet geçmişi kirlenmez" garantisinin üç ayrı katmanıdır (spec §5).
APP_NAME = "jarvis-factory"

# Recursion yasağının (değişmez 3) tek gerçeği. `tools.spawn_specialist`in adı
# ile AYNI olmak zorundadır; test_factory bunu `__name__` üzerinden pinler.
SPAWN_TOOL_NAME = "spawn_specialist"

# Dönüş sözleşmesi (spec §4): durum ∈ {tamam, adim_butcesi_doldu, sure_doldu, hata}
STATUS_OK = "tamam"
STATUS_STEP_BUDGET = "adim_butcesi_doldu"
STATUS_TIMEOUT = "sure_doldu"
STATUS_ERROR = "hata"


@dataclass(frozen=True)
class AgentTemplate:
    """Derleme anında sabit bir kalıp. `frozen=True`: bir şablon çalışma anında
    DEĞİŞTİRİLEMEZ — Kademe 1'i Kademe 3'ten ayıran şey tam olarak budur."""

    name: str
    purpose: str          # Kadir'in menüde göreceği Türkçe amaç
    instruction: str      # örneğin sistem talimatı (modele gider)
    tools: tuple[str, ...]
    max_steps: int        # tavan 1: Runner.run_async'ten sayılan olay sayısı
    ttl_seconds: int      # tavan 2: duvar saati


# Ortak talimat kuyruğu: her şablon MİSAFİRDİR ve bunu bilmelidir (§8.5
# değişmez 1). Modelin engellenen bir aracı gizlemesi İlke 4 ihlalidir.
_GUEST_RULES = """
Sen Jarvis'in ürettiği GEÇİCİ bir uzman ajansın. Kuralların:
- Sana verilen görevi yap, kapsamını KENDİN genişletme.
- Elindeki araçların dışına çıkamazsın ve yeni araç isteyemezsin.
- Bütçen sınırlı (adım sayısı ve süre). Bitirdiğinde tek seferde, Türkçe ve
  kısa bir özet yaz — Kadir yalnızca o özeti görecek.
- Bir araç politika engeline takılırsa bunu SAKLAMA, özetinde açıkça söyle.
"""

TEMPLATES: dict[str, AgentTemplate] = {
    "arastirmaci": AgentTemplate(
        name="arastirmaci",
        purpose="Bir konuyu kalıcı hafızadan ve izlenen repo'lardan toplayıp özetler.",
        instruction=(
            "Sen bir araştırmacı uzman ajansın. Verilen konuyu Kadir'in kalıcı "
            "hafızasından (search_memory), profilinden (get_user_profile) ve izlenen "
            "GitHub repo'larından (list_watched_repos, get_repo_updates) topla; "
            "bulduklarını kaynaklarıyla birlikte özetle. Bulamadığın şeyi UYDURMA, "
            "'bulunamadı' de." + _GUEST_RULES
        ),
        tools=("search_memory", "get_user_profile", "list_watched_repos",
               "get_repo_updates"),
        # Toplama işi araç başına ~2 olay (çağrı + cevap) + son özet üretir; dört
        # araç + birkaç tekrar için 24 rahat bir tavan. Süre, Kadir'in sohbet
        # turunun içinde beklediği süredir — dakikalar değil.
        max_steps=24,
        ttl_seconds=90,
    ),
    "arsivci": AgentTemplate(
        name="arsivci",
        purpose="Bir konuşmadan çıkan kalıcı bilgiyi profile ve derslere işler.",
        instruction=(
            "Sen bir arşivci uzman ajansın. Verilen metinden Kadir hakkında KALICI "
            "olan bilgiyi ayıkla ve işle: tekil gerçekleri remember_fact ile, "
            "tercih/rutin/kuralları update_user_profile ile, düzeltilmiş bir hatayı "
            "add_lesson ile yaz. Yazmadan ÖNCE search_memory ile aynı bilginin zaten "
            "olup olmadığına bak; varsa tekrar yazma. Geçici/tek seferlik ayrıntıları "
            "kalıcılaştırma." + _GUEST_RULES
        ),
        tools=("search_memory", "remember_fact", "add_lesson", "update_user_profile"),
        max_steps=20,
        ttl_seconds=60,
    ),
    "nobetci": AgentTemplate(
        name="nobetci",
        purpose="Sistem sağlığını ve bekleyen işleri derleyip rapor eder.",
        instruction=(
            "Sen bir nöbetçi uzman ajansın. check_my_vitals ile sistem sağlığını, "
            "list_reminders ile bekleyen hatırlatmaları, list_watched_repos ile izleme "
            "listesinin durumunu (son kontrol, son hata) topla ve Kadir'e tek bir kısa "
            "durum raporu yaz. Sorun yoksa 'sorun yok' demekten çekinme." + _GUEST_RULES
        ),
        tools=("check_my_vitals", "list_reminders", "list_watched_repos"),
        max_steps=16,
        ttl_seconds=45,
    ),
}


def new_instance_id() -> str:
    """Örnek kimliği. Kısa ama çakışmayacak kadar geniş: audit satırında ve
    oturum kimliğinde okunabilir kalması gerekiyor (§8.5 değişmez 5)."""
    return uuid.uuid4().hex[:8]


def session_id_for(template_name: str, instance: str) -> str:
    """Ayrı oturum kimliği (spec §5) — Kadir'in sohbet geçmişi kirlenmez."""
    return f"factory-{template_name}-{instance}"


def actor_for(template_name: str, instance: str) -> str:
    """Audit aktörü (§8.5 değişmez 5): "kim üretti, hangi kalıptan"."""
    return f"factory:{template_name}#{instance}"


def _tool_catalog() -> list:
    """`tools.ALL_TOOLS`, GEÇ import ile.

    `app.tools` bu modülü import eder (aracı orada tanımlı), dolayısıyla modül
    seviyesinde ters yönde bir import döngü kurardı. Kesişimin kaynağı yine tek
    bir yerdir: ALL_TOOLS."""
    from . import tools

    return tools.ALL_TOOLS


def resolve_tools(template: AgentTemplate, *, catalog: list | None = None) -> list:
    """Şablonun araç adlarını GERÇEK araç nesnelerine çevirir (§8.5 değişmez 2).

    İki kural, bu sırayla:
    1. `SPAWN_TOOL_NAME` MUTLAK dışlanır (değişmez 3). Şablon onu açıkça istese
       bile: recursion yasağı bir tercih değil, anayasa maddesidir.
    2. Kalanlar `ALL_TOOLS` ile kesiştirilir. Bulunamayan ad SESSİZCE atlanmaz —
       `logging.warning` ile yazılır, çünkü sessiz bir eksilme canlıda "ajan
       neden hiçbir şey yapmadı" olarak görünür ve teşhis edilemez.

    Sıra şablondaki sıradır (deterministik araç listesi = deterministik model
    davranışı)."""
    available = {getattr(fn, "__name__", None): fn
                 for fn in (_tool_catalog() if catalog is None else catalog)}
    chosen, missing = [], []
    for name in template.tools:
        if name == SPAWN_TOOL_NAME:
            logging.warning(
                "factory: '%s' şablonu %s istedi -- RECURSION YASAĞI (§8.5 değişmez 3), "
                "araç kümesine ALINMADI", template.name, SPAWN_TOOL_NAME)
            continue
        fn = available.get(name)
        if fn is None:
            missing.append(name)
            continue
        chosen.append(fn)
    if missing:
        logging.warning(
            "factory: '%s' şablonundaki şu araçlar ALL_TOOLS'ta bulunamadı ve "
            "kümeye alınmadı: %s", template.name, ", ".join(missing))
    logging.info("factory: '%s' araç kümesi çözüldü istenen=%d verilen=%d eksik=%d",
                 template.name, len(template.tools), len(chosen), len(missing))
    return chosen


def build_specialist(template: AgentTemplate, *, instance: str, audit, model,
                     catalog: list | None = None) -> Agent:
    """Türetilmiş ajanı kurar. Kademe 0'ın `agent.build_agent`'ıyla AYNI ADK
    makinesi, üç bilinçli FARK'la:

    * `approval_sink` YOK (§8.5 değişmez 1). Kırmızı bir araç çağrısı bir onay
      kartına bile dönüşmez; `policy._red_block_text` sink'siz dala düşer ve düz
      engel metnini verir. Misafir, Kadir adına onay isteyemez.
    * `zone_resolver` YOK. Çözücü bir bölgeyi yalnızca GEVŞETEBİLİR (green/
      yellow); vermemek kesinlikle daha kısıtlayıcıdır ve "misafir yüzeyi kayıt
      defteriyle genişlemez" (§4.9'un fabrikaya izdüşümü) bunu gerektirir.
    * `actor` fabrika örneğidir (§8.5 değişmez 5), "orchestrator" değil.

    `model`, orkestratörün kullandığı model fabrikasından gelir (bkz.
    `_default_model`) — örnek Kadir'in beyniyle aynı beyni kullanır, ama
    misafirin yetkileriyle."""
    return Agent(
        name=f"factory_{template.name}_{instance}",
        model=model,
        instruction=template.instruction,
        tools=resolve_tools(template, catalog=catalog),
        before_tool_callback=policy.make_policy_callback(
            audit, actor=actor_for(template.name, instance)),
    )


def _final_text(event) -> str | None:
    """Olaydan nihai cevap metni (main.run_turn ile AYNI okuma), yoksa None."""
    if event.is_final_response() and event.content and event.content.parts:
        return event.content.parts[0].text or ""
    return None


async def run_specialist(template: AgentTemplate, goal: str, *, runner, user_id: str,
                         instance: str, clock: Callable[[], float] = time.monotonic) -> dict:
    """Örneği tek bir çağrı boyunca koşturur ve İKİ BAĞIMSIZ TAVANI uygular
    (§8.5 değişmez 4).

    Tavan 1 — ADIM: `run_async`'ten gelen olaylar sayılır. Hızlı bir döngü
    saniyeler içinde yüzlerce olay üretebilir; süre tavanı onu yakalamaz.

    Tavan 2 — DUVAR SAATİ, iki mekanizmayla:
      a) her olaydan SONRA `clock()` kontrolü. Enjekte edilebilir olması testin
         gerçek zaman beklemesini gereksiz kılar (deterministik pim).
      b) tüm döngüyü saran `asyncio.timeout`. (a) tek başına YETMEZ: hiç olay
         üretmeyen bir koşuda o kontrol hiç çalışmaz ve tur sonsuza kadar Kadir'i
         bekletirdi. İkisi de aynı `ttl_seconds`i, aynı birimi (saniye) kullanır.

    (b)'NİN SINIRI, açıkça: `asyncio.timeout` yalnızca bir `await` noktasında iş
    görür. Sevkiyattaki araçlar `tools.ALL_TOOLS`ta `tools._off_loop` sarmalıyla
    (to_thread) durur, yani ADK'nın araç çağrısı bir `await` noktasıdır ve tavan
    o bekleyişi KESER — sarmasız hâlde ölçülmüştü: ttl=0.2 sn iken 3.0 sn süren
    senkron bir çağrı 3.0 sn sürüyor ve o sürede event loop'un tamamı (sohbet,
    ses WS, misafir kapısı) bloklu kalıyordu (pin: test_factory'nin bloklayan
    çifti + tests/test_offloop.py). İki kalıntı sınır, gizlenmeden:

    * Kesilen BEKLEYİŞtir, iş değil: to_thread iptal edilemez, iş parçacığı
      arka planda sonuna kadar koşar. "TTL var" demek "en fazla TTL kadar
      beklersin" demektir; "aracın yan etkisi gerçekleşmez" demek DEĞİLDİR —
      süresi kesilmiş bir koşunun Firestore yazması sonradan tamamlanabilir.
    * Sarmadan geçmeyen, loop'u satır içi bloklayan kod (ör. üçüncü parti bir
      kütüphane) hâlâ tavanı aşar; onun üst sınırı altta yatan istemcinin kendi
      deadline'ıdır.

    Tavan semantiği tasks.py ile AYNI: tavan "tam dolduğunda" durum tavanın
    durumudur — o adım başarılı olsa bile (tasks.step_once'ın
    `budget_exhausted` dalıyla birebir). Elde edilmiş metin yine `sonuc`ta
    döner, yani bilgi kaybı yoktur; sadece rapor muhafazakârdır.

    FIRLATMAZ (İlke 4): runner hatası `durum="hata"` + Türkçe gözlemdir."""
    session_id = session_id_for(template.name, instance)
    started = clock()
    steps = 0
    sonuc = ""
    durum = STATUS_OK

    try:
        content = types.Content(role="user", parts=[types.Part(text=goal)])
        async with asyncio.timeout(template.ttl_seconds):
            # aclosing: tavana dayanıp `break` ettiğimizde ADK'nın async
            # generator'ı DERHAL kapatılsın -- yoksa yarıda kesilmiş bir koşu
            # çöp toplayıcıya kalır ve modelin/MCP oturumunun kapanışı gecikir.
            async with aclosing(runner.run_async(
                    user_id=user_id, session_id=session_id, new_message=content)) as stream:
                async for event in stream:
                    steps += 1
                    text = _final_text(event)
                    if text is not None:
                        sonuc = text
                    if steps >= template.max_steps:
                        durum = STATUS_STEP_BUDGET
                        break
                    if clock() - started >= template.ttl_seconds:
                        durum = STATUS_TIMEOUT
                        break
    except TimeoutError:
        # asyncio.timeout: olay ÜRETMEDEN askıda kalan çağrı.
        durum = STATUS_TIMEOUT
        logging.warning("factory: %s sert süre tavanına dayandı ttl=%ss adim=%d",
                        actor_for(template.name, instance), template.ttl_seconds, steps)
    except Exception as exc:
        durum = STATUS_ERROR
        sonuc = f"Uzman ajan çalışırken hata aldı: {exc}"
        logging.exception("factory: %s koşusu hata verdi adim=%d",
                          actor_for(template.name, instance), steps)

    logging.info(
        "factory: koşu bitti actor=%s durum=%s adim=%d/%d sure=%.1fs/%ss sonuc_uzunluk=%d",
        actor_for(template.name, instance), durum, steps, template.max_steps,
        clock() - started, template.ttl_seconds, len(sonuc))
    return {"sablon": template.name, "ornek_id": instance, "adim": steps,
            "sonuc": sonuc, "durum": durum}


def _template_from_doc(doc: dict) -> "AgentTemplate | None":
    """Kayıt defteri dokümanından şablon kurar; bozuksa None (fail-closed).

    İki bilinçli adım (spec §6):
    * Doküman ALANLARI yeniden doğrulanır — elle yazılmış/bozulmuş bir kayıt
      (kırmızı araç sokulmuş, tavan şişirilmiş) şablona dönüşemez.
    * `_GUEST_RULES` BURADA eklenir; depolanan metne güvenilmez — veri
      kanalından misafir anayasası gevşetilemez."""
    from . import agent_registry

    try:
        tool_names = list(doc.get("tools") or [])
        problem = agent_registry.validate_definition(
            name=str(doc.get("name") or ""), purpose=str(doc.get("purpose") or ""),
            instruction=str(doc.get("instruction") or ""), tool_names=tool_names,
            why=str(doc.get("why") or "-"), evidence=str(doc.get("evidence") or "-"),
            max_steps=doc.get("max_steps"), ttl_seconds=doc.get("ttl_seconds"))
        if problem:
            logging.warning("factory: kayıt defteri dokümanı reddedildi name=%r -- %s",
                            doc.get("name"), problem)
            return None
        return AgentTemplate(
            name=str(doc["name"]).strip(),
            purpose=str(doc["purpose"]),
            instruction=str(doc["instruction"]) + _GUEST_RULES,
            tools=tuple(tool_names),
            max_steps=int(doc["max_steps"]),
            ttl_seconds=int(doc["ttl_seconds"]),
        )
    except Exception:
        logging.exception("factory: kayıt defteri dokümanı şablona çevrilemedi name=%r",
                          doc.get("name"))
        return None


def _registry_template(template_name: str) -> "AgentTemplate | None":
    """Üretim kayıt-defteri okuması: spawn anında CANLI tek doküman.

    Araç tarafından bilinçli fark (spec §6): MCP toolset'leri açılışta
    bağlanır, veri şablonuysa onaydan hemen sonra kullanılabilir — "üretim
    onay anında gerçekleşir" (§8.5 Kademe 2). FIRLATMAZ: her hata None'dır
    (çağıran menüye düşer), sebep loglanır."""
    from . import agent_registry
    from . import tools as tools_mod

    try:
        doc = agent_registry.get(tools_mod._memory.db, template_name)
        if not doc or doc.get("status") != agent_registry.STATUS_GRANTED:
            return None
        return _template_from_doc(doc)
    except Exception:
        logging.exception("factory: kayıt defteri okunamadı sablon=%r", template_name)
        return None


def _registry_menu() -> list[dict]:
    """Menü için granted kayıtlar; hata boş listedir (menü süs değil ama
    yokluğu spawn'ı düşürmemeli)."""
    from . import agent_registry
    from . import tools as tools_mod

    try:
        return agent_registry.list_granted(tools_mod._memory.db)
    except Exception:
        logging.exception("factory: kayıt defteri menüsü okunamadı")
        return []


def unknown_template_reply(template_name, registered: list[dict] | None = None) -> dict:
    """Bilinmeyen şablon → Türkçe gözlem + menü (İlke 4: FIRLATMAZ).

    Menü iki kaynaklıdır (Kademe 2): sevkiyat şablonları + kayıt defterindeki
    kalıcı ajanlar, her satır kaynağını söyler. Menü olmadan model kör kalır
    ve aynı yanlış adı tekrar dener."""
    menu = [
        {"ad": t.name, "amac": t.purpose, "araclar": list(t.tools),
         "kaynak": "sevkiyat"}
        for t in sorted(TEMPLATES.values(), key=lambda t: t.name)
    ] + [
        {"ad": d.get("name"), "amac": d.get("purpose"),
         "araclar": list(d.get("tools") or []), "kaynak": "kayit_defteri"}
        for d in (registered or [])
    ]
    return {
        "durum": STATUS_ERROR,
        "hata": (f"'{template_name}' diye bir ajan şablonu yok. "
                 "Aşağıdakilerden birini seç."),
        "kullanilabilir_sablonlar": menu,
    }


def _default_model():
    """Üretim modeli: orkestratörün kullandığı AYNI model fabrikası.

    GEÇ import — `app.main` -> `app.agent` -> `app.tools` -> `app.factory`
    zinciri modül seviyesinde bir döngü kurar. Çağrı anında main zaten
    yüklüdür (bu kod ancak çalışan bir ADK turunun içinden çağrılır).

    Kopyalamak yerine main'e uzanmanın sebebi "tek kavram tek isim": proxy'ye
    bağlı `Gemini` nesnesi mi yoksa düz model adı mı kullanılacağı kararı
    (`config.LLM_BASE_URL` + katalog çözümü) tek bir yerde durmalı."""
    from .main import _build_text_model

    return _build_text_model()


def _default_runner_factory(agent: Agent, session_service) -> Runner:
    """Örneğin KENDİ Runner'ı (spec §5). Kadir'in runner'ı paylaşılmaz: ajan,
    araç kümesi ve politika aktörü farklıdır."""
    return Runner(app_name=APP_NAME, agent=agent, session_service=session_service)


async def spawn(template_name, goal: str, *, user_id: str, audit, model=None,
                instance: str | None = None, runner_factory=None,
                clock: Callable[[], float] = time.monotonic,
                templates: dict | None = None,
                registry_lookup=None, registry_menu=None) -> dict:
    """Şablondan bir örnek üretir, koşturur ve raporunu döner (spec §4 + K2 §6).

    Şablon çözümü iki katmanlı: önce derleme-anı TEMPLATES (her zaman kazanır),
    yoksa kayıt defteri (Kademe 2). Enjeksiyon noktaları (`model`, `instance`,
    `runner_factory`, `clock`, `templates`, `registry_lookup`, `registry_menu`)
    YALNIZCA testler içindir; üretimde hepsi None/varsayılan geçilir.

    FIRLATMAZ: bilinmeyen şablon menüye, kurulum hatası `durum="hata"`ya döner
    (İlke 4)."""
    known = TEMPLATES if templates is None else templates
    key = template_name.strip() if isinstance(template_name, str) else ""
    template = known.get(key)
    if template is None and key:
        template = (registry_lookup or _registry_template)(key)
    if template is None:
        logging.info("factory: bilinmeyen şablon istendi=%r", template_name)
        return unknown_template_reply(template_name,
                                      registered=(registry_menu or _registry_menu)())

    instance = instance or new_instance_id()
    logging.info("factory: örnek kuruluyor actor=%s max_steps=%d ttl=%ss hedef=%r",
                 actor_for(template.name, instance), template.max_steps,
                 template.ttl_seconds, str(goal)[:200])
    try:
        agent = build_specialist(template, instance=instance, audit=audit,
                                 model=model if model is not None else _default_model())
        # Örneğe ÖZEL oturum servisi: Kadir'in sohbet oturumları başka bir
        # servistedir, yani bu koşu onun geçmişine yapısal olarak dokunamaz.
        session_service = InMemorySessionService()
        runner = (runner_factory or _default_runner_factory)(agent, session_service)
        await session_service.create_session(
            app_name=APP_NAME, user_id=user_id,
            session_id=session_id_for(template.name, instance))
    except Exception as exc:
        logging.exception("factory: örnek kurulamadı actor=%s",
                          actor_for(template.name, instance))
        return {"sablon": template.name, "ornek_id": instance, "adim": 0,
                "sonuc": f"Uzman ajan kurulamadı: {exc}", "durum": STATUS_ERROR}

    return await run_specialist(template, goal, runner=runner, user_id=user_id,
                                instance=instance, clock=clock)
