"""Faz Y4.2 — Ajan Fabrikası Kademe 1 (Kalıphane).

Bu dosya §8.5'in fabrika anayasasını PİNLER. Her testin altında "bu iddiayı
hangi mutasyon öldürür?" sorusunun cevabı yazılıdır; cevabı olmayan test bu
dosyaya girmez.

Taşıyıcı pimler (silinirse fabrika sessizce güvensizleşir):

- `test_a_red_tool_is_plainly_blocked_and_no_approval_card_is_created`
  (§8.5 değişmez 1): türetilmiş ajanın politika callback'i `approval_sink`
  ALMAZ. Kontrol grubu aynı testin içinde: AYNI db üzerinde gerçekten yazan bir
  sink'le kurulmuş orkestratör callback'i bir onay dokümanı ÜRETİR — yani
  "approvals boş" iddiası boş bir iddia değildir.
- `test_spawn_specialist_is_excluded_even_when_the_template_asks_for_it`
  (§8.5 değişmez 3): recursion yasağı bir filtre değil, MUTLAK dışlamadır.
- `test_step_cap_fires_while_the_clock_stands_still` +
  `test_time_cap_fires_while_the_step_budget_is_untouched` (§8.5 değişmez 4):
  iki tavan BAĞIMSIZDIR; her test diğer tavanın ateşlenemeyeceği bir kurulumda
  koşar, yoksa "tek tavan iki isimle" da yeşil görünürdü.

Gerçek LLM çağrısı YOK: Runner enjekte edilir (`runner_factory`), sahtesi
olayları senaryodan üretir. ADK Agent nesnesi GERÇEKTİR — araç kümesi ve
politika callback'i onun üzerinden okunur, çünkü asıl pinlenen şey odur.
"""
import asyncio
import logging
from types import SimpleNamespace

import pytest

from app import approvals, config, factory, policy, tools as tools_mod
from tests.fakes import FakeDB

MODEL = "fake-model"
USER = "owner@example.com"


class FakeAudit:
    def __init__(self, db=None):
        self.entries = []
        # policy._count_tool_call db'yi audit üzerinden arar; None bırakmak
        # sayaç yazımını sessizce atlatır (üretimde FirestoreAudit.db vardır).
        self.db = db

    def write(self, entry):
        self.entries.append(entry)


def _tool(name):
    return SimpleNamespace(name=name)


class FakeEvent:
    """ADK Event'in politika/olay döngüsünün gördüğü kadarı."""

    def __init__(self, text=None):
        self._text = text
        self.content = (
            SimpleNamespace(parts=[SimpleNamespace(text=text)]) if text is not None else None
        )

    def is_final_response(self):
        return self._text is not None


class FakeRunner:
    """Enjekte edilen Runner: olayları senaryodan üretir, LLM'e gitmez.

    `on_event` her olaydan ÖNCE çağrılır — sahte saati ilerletmek için (süre
    tavanı testi). `calls` çağrı argümanlarını saklar (oturum kimliği pimi)."""

    def __init__(self, events, on_event=None):
        self._events = list(events)
        self._on_event = on_event
        self.calls = []

    async def run_async(self, *, user_id, session_id, new_message):
        self.calls.append({"user_id": user_id, "session_id": session_id,
                           "new_message": new_message})
        for event in self._events:
            if self._on_event is not None:
                self._on_event()
            yield event


def _runner_factory(runner):
    return lambda agent, session_service: runner


def _template(**over):
    base = dict(name="test_sablonu", purpose="test", instruction="Test talimatı.",
                tools=("search_memory",), max_steps=50, ttl_seconds=30)
    base.update(over)
    return factory.AgentTemplate(**base)


async def _spawn(template, *, runner, goal="hedef", clock=None, audit=None, instance="i1"):
    """factory.spawn'ı ŞABLON NESNESİYLE koşturur: TEMPLATES'e test şablonu
    enjekte etmeden (global sözlüğü kirletmeden) aynı üretim yolunu kullanır."""
    return await factory.spawn(
        template.name, goal, user_id=USER, audit=audit or FakeAudit(), model=MODEL,
        instance=instance, runner_factory=_runner_factory(runner),
        clock=clock or (lambda: 0.0), templates={template.name: template},
    )


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 1 (§8.5): misafir muamelesi — kırmızıya asla, onay kartına bile değil
# ---------------------------------------------------------------------------


def test_a_red_tool_is_plainly_blocked_and_no_approval_card_is_created():
    """TAŞIYICI PİM (§8.5 değişmez 1, spec §2.1): türetilmiş ajanın politika
    callback'ine `approval_sink` VERİLMEZ. Kırmızı bir araç çağrısı onu bir onay
    kartına bile çeviremez — düz engellenir.

    ÖLDÜREN MUTASYON: `build_specialist` içindeki callback'e çalışan bir
    `approval_sink=` eklemek. İki bağımsız iddia birlikte hem çalışan hem
    çöken sink'i yakalar:
      * metin `policy.RED_BLOCK_TEMPLATE` ile BİREBİR aynı (çalışan bir sink
        farklı metin döndürür -> kırılır),
      * `approvals` koleksiyonuna hiçbir şey yazılmaz (yazan bir sink -> kırılır).

    Kontrol grubu aşağıda: AYNI db + AYNI kırmızı araçla kurulmuş
    ORKESTRATÖR callback'i bir onay dokümanı üretiyor. Yani "approvals boş"
    iddiası, sink makinesinin bu db'de çalışmadığından değil, fabrikanın ona
    hiç uğramadığındandır."""
    db = FakeDB()

    def real_sink(tool_name, args, tool_context):
        approvals.request(db, user_id=USER, kind=approvals.KIND_TOOL_CALL,
                          title=f"'{tool_name}' çalıştırılsın mı?", detail="detay",
                          tool_name=tool_name, tool_args=args, zone=config.ZONE_RED,
                          session_id="chat-1")
        return "ONAY KARTI GÖNDERİLDİ: Kadir'in kararını bekliyorum."

    # --- kontrol grubu: orkestratör yolu onay kartı ÜRETİR
    orchestrator = policy.make_policy_callback(FakeAudit(), approval_sink=real_sink)
    kontrol = orchestrator(_tool("cancel_reminder"), {"reminder_id": "r1"}, None)
    assert "ONAY KARTI GÖNDERİLDİ" in kontrol["result"]
    assert len(db.collections[approvals.COLLECTION].docs) == 1

    # --- fabrika yolu: aynı db, aynı kırmızı araç, HİÇBİR yeni onay.
    # audit db'yi TAŞIYOR (üretimde FirestoreAudit.db; policy._count_tool_call
    # onu zaten böyle okur), yani "sink'i audit.db'den kur" mutasyonu gerçekten
    # yazabilir -- ikinci iddia boş bir iddia değil.
    agent = factory.build_specialist(_template(), instance="i1", audit=FakeAudit(db),
                                     model=MODEL)
    sonuc = agent.before_tool_callback(_tool("cancel_reminder"), {"reminder_id": "r1"}, None)

    assert sonuc == {"result": policy.RED_BLOCK_TEMPLATE.format(tool_name="cancel_reminder")}
    assert len(db.collections[approvals.COLLECTION].docs) == 1, (
        "türetilmiş ajan onay merkezine yazdı — misafir kırmızıya dokunamaz")


def test_an_unknown_tool_name_is_red_for_the_specialist_too():
    """Bilinmeyen ad = kırmızı (DEFAULT_ZONE) ve yine düz engel. Türetilmiş
    ajana `zone_resolver` VERİLMEZ: çözücü yalnızca GEVŞETEBİLİR (green/yellow),
    misafir yüzeyi kayıt defteriyle genişlemez.

    ÖLDÜREN MUTASYON: build_specialist'e `zone_resolver=` eklemek + kayıt
    defterinde o adı granted yapmak — bu test o adın yine kırmızı kalmasını
    istediği için kırılır (aşağıdaki registry testinde uçtan uca)."""
    audit = FakeAudit()
    agent = factory.build_specialist(_template(), instance="i1", audit=audit, model=MODEL)

    sonuc = agent.before_tool_callback(_tool("rm_rf"), {}, None)

    assert "POLİTİKA ENGELİ" in sonuc["result"]
    assert audit.entries[-1]["decision"] == "block"
    assert audit.entries[-1]["zone"] == config.ZONE_RED


def test_a_granted_registry_entry_cannot_widen_the_specialist():
    """§8.5 değişmez 1'in kayıt-defteri kanadı: Kadir'in orkestratöre kazandırdığı
    bir MCP sunucusu, türetilmiş ajanın bölgesini AÇMAZ — çünkü ona hiç çözücü
    verilmez.

    ÖLDÜREN MUTASYON: build_specialist'in callback'ine
    `zone_resolver=tool_registry.make_zone_resolver(getattr(audit, "db", None))`
    eklemek.

    audit FakeAudit(db) İLE kurulur, FakeAudit() ile DEĞİL: policy._count_tool_call
    db'yi audit üzerinden bulur, yani db'nin callback'e ulaşabildiği TEK yol budur.
    db=None bırakıldığında yukarıdaki mutasyon çözücüyü None ile kurar, hiçbir kayıt
    okunamaz ve test mutasyonu öldüremez -- gözden geçirmede yakalandı: bu hâliyle
    süit mutasyonla birlikte 696/696 yeşil kalıyordu, yani iddia ölçülmüyordu."""
    from app import tool_registry

    db = FakeDB()
    tool_registry.grant(db, name="github_mcp", kind=tool_registry.KIND_MCP,
                        zone=config.ZONE_GREEN, why="gerekçe", approval_id="ap1",
                        mcp={"transport": "http", "url": "https://mcp.example/mcp"})
    agent = factory.build_specialist(_template(), instance="i1", audit=FakeAudit(db),
                                     model=MODEL)

    sonuc = agent.before_tool_callback(_tool("github_mcp_list_prs"), {}, None)

    assert sonuc is not None and "POLİTİKA ENGELİ" in sonuc["result"]


def test_a_yellow_tool_still_runs_for_the_specialist():
    """"Yeşil + sarı ile başlar": sarı bir araç engellenmez, aksi hâlde değişmez
    1'in ilk yarısı sessizce kaybolurdu (her şeyi engelleyen bir fabrika da bu
    dosyadaki kırmızı testlerini geçerdi)."""
    audit = FakeAudit()
    agent = factory.build_specialist(_template(), instance="i1", audit=audit, model=MODEL)

    assert agent.before_tool_callback(_tool("update_user_profile"), {}, None) is None
    assert audit.entries[-1]["decision"] == "allow"


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 2 (§8.5): araç kümesi = şablon ∩ ALL_TOOLS, eksikler LOGLANIR
# ---------------------------------------------------------------------------


def _catalog_tool(name):
    """Aracın ALL_TOOLS'taki hâli. Senkron araçlar katalogda `tools._off_loop`
    (to_thread) sarmalıyla durur; resolve_tools kataloğun KENDİ nesnelerini
    döndürür, modül seviyesindeki senkron adları değil. Kimlik pinleri bu
    yüzden katalog üzerinden kurulur (zaten async olan spawn_specialist için
    ikisi aynı nesnedir — tests/test_offloop.py bunu ayrıca pinler)."""
    return next(f for f in tools_mod.ALL_TOOLS if f.__name__ == name)


def test_the_tool_set_is_the_intersection_with_all_tools():
    tpl = _template(tools=("search_memory", "check_my_vitals"))
    assert factory.resolve_tools(tpl) == [_catalog_tool("search_memory"),
                                          _catalog_tool("check_my_vitals")]


def test_a_template_name_that_is_not_a_real_tool_is_dropped_and_logged(caplog):
    """§8.5 değişmez 2: "Şablonda yazan ama bulunamayan ad SESSİZCE ATLANMAZ."

    ÖLDÜREN MUTASYON: `resolve_tools` içindeki eksik-ad `logging.warning`
    satırını silmek (caplog iddiası kırılır) ya da bilinmeyen adı kümeye
    almak (ilk iddia kırılır)."""
    tpl = _template(tools=("search_memory", "hayali_arac"))

    with caplog.at_level(logging.WARNING):
        chosen = factory.resolve_tools(tpl)

    assert chosen == [_catalog_tool("search_memory")]
    assert "hayali_arac" in caplog.text


def test_every_shipped_template_resolves_to_real_tools(caplog):
    """Üretim şablonları BUGÜN mevcut araçlarla çalışır (spec §3: "hayali
    yetenek yok"). Bir şablona yanlış yazılmış bir ad girerse bu test onu
    yakalar — aksi hâlde hata ancak canlıda, sessizce eksilmiş bir araç
    kümesi olarak görülürdü."""
    with caplog.at_level(logging.WARNING):
        for tpl in factory.TEMPLATES.values():
            assert len(factory.resolve_tools(tpl)) == len(tpl.tools), tpl.name
    assert "bulunamadı" not in caplog.text


def test_the_specialist_agent_carries_exactly_the_resolved_tools():
    tpl = _template(tools=("search_memory", "check_my_vitals"))
    agent = factory.build_specialist(tpl, instance="i1", audit=FakeAudit(), model=MODEL)
    assert list(agent.tools) == [_catalog_tool("search_memory"),
                                 _catalog_tool("check_my_vitals")]


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 3 (§8.5): recursion yasağı — MUTLAK dışlama
# ---------------------------------------------------------------------------


def test_spawn_specialist_is_excluded_even_when_the_template_asks_for_it(caplog):
    """TAŞIYICI PİM (§8.5 değişmez 3): "Ajan üretemez." Şablon onu AÇIKÇA istese
    bile araç kümesine giremez — bu bir filtre değil, mutlak dışlamadır.

    Test bilerek en kötü hâli kurar: şablon `spawn_specialist`'i adıyla ister
    ve o araç ALL_TOOLS'ta GERÇEKTEN vardır (aşağıdaki test onu da pinler).

    ÖLDÜREN MUTASYON: `resolve_tools` içindeki `if name == SPAWN_TOOL_NAME:
    continue` dalını silmek — kesişim kuralı adı bulur ve fabrika kendini
    üretebilir hâle gelir."""
    tpl = _template(tools=("search_memory", factory.SPAWN_TOOL_NAME))

    with caplog.at_level(logging.WARNING):
        chosen = factory.resolve_tools(tpl)

    assert chosen == [_catalog_tool("search_memory")]
    assert tools_mod.spawn_specialist not in chosen
    assert factory.SPAWN_TOOL_NAME in caplog.text


def test_the_excluded_name_really_is_a_live_tool():
    """Yukarıdaki dışlamanın ANLAMLI olduğunun kanıtı: `spawn_specialist`
    ALL_TOOLS'ta var, yani dışlama olmasa kesişim onu bulurdu."""
    assert tools_mod.spawn_specialist in tools_mod.ALL_TOOLS
    assert tools_mod.spawn_specialist.__name__ == factory.SPAWN_TOOL_NAME


def test_no_shipped_agent_can_spawn_another_one():
    tpl = _template(tools=("search_memory", factory.SPAWN_TOOL_NAME))
    agent = factory.build_specialist(tpl, instance="i1", audit=FakeAudit(), model=MODEL)
    assert [getattr(t, "__name__", None) for t in agent.tools] == ["search_memory"]


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 4 (§8.5): İKİ BAĞIMSIZ TAVAN — adım sayısı VE duvar saati
# ---------------------------------------------------------------------------


async def test_step_cap_fires_while_the_clock_stands_still():
    """TAŞIYICI PİM (§8.5 değişmez 4, spec §5): hızlı bir döngü saniyeler içinde
    yüzlerce adım atar — süre tavanı onu YAKALAMAZ.

    Saat bilerek DONDURULMUŞ (hep 0.0): süre tavanı ateşlenemez, dolayısıyla
    yeşil sonuç yalnızca adım tavanının gerçekten var olmasıyla açıklanabilir.

    ÖLDÜREN MUTASYON: `run_specialist` içindeki `steps >= max_steps` dalını
    silmek -> durum "tamam", adım 100 olur."""
    runner = FakeRunner([FakeEvent() for _ in range(100)])

    out = await _spawn(_template(max_steps=3, ttl_seconds=30), runner=runner,
                       clock=lambda: 0.0)

    assert out["durum"] == factory.STATUS_STEP_BUDGET
    assert out["adim"] == 3


async def test_time_cap_fires_while_the_step_budget_is_untouched():
    """TAŞIYICI PİM (§8.5 değişmez 4, spec §5): tek bir araç çağrısı dakikalarca
    sürebilir — adım tavanı onu YAKALAMAZ.

    Adım tavanı bilerek 1000: ateşlenmesi imkânsız. Sahte saat her olayda 10 sn
    ilerler, TTL 15 sn -> ikinci olaydan sonra durur.

    ÖLDÜREN MUTASYON: `run_specialist` içindeki süre kontrolünü silmek ->
    durum "tamam", adım 5 olur."""
    saat = {"t": 0.0}

    def tik():
        saat["t"] += 10.0

    runner = FakeRunner([FakeEvent() for _ in range(5)], on_event=tik)

    out = await _spawn(_template(max_steps=1000, ttl_seconds=15), runner=runner,
                       clock=lambda: saat["t"])

    assert out["durum"] == factory.STATUS_TIMEOUT
    assert out["adim"] == 2


async def test_a_run_that_finishes_inside_both_caps_is_reported_as_done():
    """Kontrol grubu: her iki tavan da bol olduğunda durum "tamam" ve nihai
    metin geri gelir. Bu olmasaydı "her koşuyu keser" bir uygulama da yukarıdaki
    iki tavan testini geçerdi."""
    runner = FakeRunner([FakeEvent(), FakeEvent("Özet hazır.")])

    out = await _spawn(_template(max_steps=50, ttl_seconds=30), runner=runner)

    assert out["durum"] == factory.STATUS_OK
    assert out["sonuc"] == "Özet hazır."
    assert out["adim"] == 2


async def test_a_hung_call_that_never_yields_an_event_still_times_out():
    """Olay BAŞINA kontrol yetmez: hiç olay üretmeyen (askıda kalmış) bir çağrıda
    o kontrol hiç çalışmaz. Sert sınır `asyncio.timeout`tur.

    ÖLDÜREN MUTASYON: `run_specialist`'teki `asyncio.timeout(...)` sarmalını
    silmek -> test 5 sn askıda kalır ve pytest zaman aşımına düşer/başarısız
    olur (sahte saat de ilerlemediği için olay-başına kontrol kurtaramaz)."""

    class HangingRunner:
        calls = []

        async def run_async(self, *, user_id, session_id, new_message):
            await asyncio.sleep(5)
            yield FakeEvent("hiç gelmeyecek")

    out = await _spawn(_template(max_steps=1000, ttl_seconds=0.05),
                       runner=HangingRunner(), clock=lambda: 0.0)

    assert out["durum"] == factory.STATUS_TIMEOUT
    assert out["adim"] == 0


async def test_a_BLOCKING_tool_call_overruns_the_cap_and_we_say_so():
    """Sert tavanın SINIRI, gizlenmeden pinlenir.

    `asyncio.timeout` yalnız bir `await` noktasında iş görür. Sevkiyattaki
    araçlar artık ALL_TOOLS'ta `tools._off_loop` (to_thread) sarmalıyla sunulur
    ve bu sınırın DIŞINA çıktı (bir alttaki test + tests/test_offloop.py). Bu
    test kalan yarıyı belgeler: sarmadan GEÇMEYEN, loop'u satır içi bloklayan
    kod (ör. üçüncü parti bir kütüphanenin senkron çağrısı) tavanı hâlâ aşar
    ve o sürede event loop'un tamamı durur.

    Yukarıdaki askı testi `await asyncio.sleep(5)` kullanıyor, yani AWAIT EDEN
    bir askı: üretimin bu araçlar için hiç üretmediği bir hâl. Bu oturumda tam
    da bu sınıftan bir hata (üretimin üretmediği olay dizisini ölçen test) bir
    Critical'ı kaçırdı; o yüzden gerçek hâl ayrıca ölçülüyor.

    Test bir GARANTİ değil, bir BELGE: asyncio'nun kendisi değişmedikçe bu
    davranış değişmez."""
    import time as _time

    class BlockingRunner:
        async def run_async(self, *, user_id, session_id, new_message):
            _time.sleep(0.30)          # senkron araç çağrısı gibi: loop'u bloklar
            yield FakeEvent("geç geldi")

    basladi = _time.monotonic()
    out = await _spawn(_template(max_steps=1000, ttl_seconds=0.05),
                       runner=BlockingRunner(), clock=_time.monotonic)
    gecen = _time.monotonic() - basladi

    # Tavan 0.05 sn; gerçekte en az 0.30 sn sürdü -- yani sert sınır BU HÂLİ
    # bağlamıyor. DATA: aşım oranı ~6x.
    assert gecen >= 0.30, f"bloklayan çağrı kesilmiş görünüyor ({gecen:.3f} sn)"
    # Ve tavan yine de UYGULANIYOR: ilk olaydan sonraki saat kontrolü yakalıyor.
    assert out["durum"] == factory.STATUS_TIMEOUT


async def test_a_wrapped_blocking_tool_is_cut_by_the_time_cap():
    """Yukarıdaki sınırın KAPANAN yarısı (tools._off_loop dilimi): sevkiyattaki
    araçlar ALL_TOOLS'ta to_thread sarmalıyla durur, ADK'nın araç çağrısı bir
    `await` noktası olur ve sert tavan bekleyişi KESER.

    Kontrol grubu bir üstteki test: AYNI 0.30 sn'lik blok sarmasız hâlde tavanı
    ~6x aşıyor; burada sarılı hâlde ttl=0.05'te kesiliyor. Kesilen BEKLEYİŞtir —
    to_thread iptal edilemez, iş parçacığı arka planda sonuna kadar koşar, yani
    aracın yan etkisi yine gerçekleşebilir (bkz. run_specialist docstring).

    ÖLDÜREN MUTASYON: `run_specialist`'teki `asyncio.timeout` sarmalını silmek
    bu koşuyu 0.30 sn'ye uzatır ve süre assert'i kırılır; ALL_TOOLS'taki
    sarmayı kaldırmaksa test_offloop'u kırar."""
    import time as _time

    class WrappedToolRunner:
        async def run_async(self, *, user_id, session_id, new_message):
            await tools_mod._off_loop(lambda: _time.sleep(0.30))()
            yield FakeEvent("geç geldi")

    basladi = _time.monotonic()
    out = await _spawn(_template(max_steps=1000, ttl_seconds=0.05),
                       runner=WrappedToolRunner(), clock=_time.monotonic)
    gecen = _time.monotonic() - basladi

    assert out["durum"] == factory.STATUS_TIMEOUT
    assert gecen < 0.25, f"sarılı araç bekleyişi kesilmedi ({gecen:.3f} sn)"


async def test_a_runner_failure_becomes_an_observation_not_an_exception():
    """İlke 4: araç FIRLATMAZ, gözlem döner."""

    class BoomRunner:
        async def run_async(self, *, user_id, session_id, new_message):
            raise RuntimeError("model patladı")
            yield  # pragma: no cover -- gövdeyi async generator yapar

    out = await _spawn(_template(), runner=BoomRunner())

    assert out["durum"] == factory.STATUS_ERROR
    assert "model patladı" in out["sonuc"]


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 5 (§8.5): audit izi — "kim üretti, hangi kalıptan"
# ---------------------------------------------------------------------------


def test_every_tool_call_is_audited_as_the_factory_instance():
    """TAŞIYICI PİM (§8.5 değişmez 5): `actor="factory:<şablon>#<örnek>"`.

    ÖLDÜREN MUTASYON: `build_specialist`ten `actor=` argümanını silmek ->
    satır "orchestrator" olarak yazılır ve türetilmiş ajanın yaptığı iş
    Kadir'in kendi ajanının yaptığı işten AYIRT EDİLEMEZ hâle gelir."""
    audit = FakeAudit()
    agent = factory.build_specialist(_template(name="arastirmaci"), instance="ab12cd34",
                                     audit=audit, model=MODEL)

    agent.before_tool_callback(_tool("search_memory"), {"query": "x"}, None)

    assert audit.entries[-1]["actor"] == "factory:arastirmaci#ab12cd34"
    assert audit.entries[-1]["tool"] == "search_memory"


def test_a_blocked_call_is_audited_as_the_factory_instance_too():
    """Engellenen çağrı da izin bir parçasıdır: aksi hâlde bir fabrika örneğinin
    neyi denediği kayıtsız kalırdı."""
    audit = FakeAudit()
    agent = factory.build_specialist(_template(name="nobetci"), instance="ff00",
                                     audit=audit, model=MODEL)

    agent.before_tool_callback(_tool("cancel_reminder"), {}, None)

    assert audit.entries[-1]["actor"] == "factory:nobetci#ff00"
    assert audit.entries[-1]["decision"] == "block"


def test_the_orchestrator_actor_is_unchanged():
    """REGRESYON PİMİ: `actor` parametresi eklendi diye orkestratörün audit
    satırı değişmemeli — Y4.2 öncesi her satır "orchestrator"dı."""
    audit = FakeAudit()
    policy.make_policy_callback(audit)(_tool("get_user_profile"), {}, None)
    assert audit.entries[-1]["actor"] == "orchestrator"


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 6 (§8.5 / spec §5): ayrı oturum kimliği — sohbet geçmişi kirlenmez
# ---------------------------------------------------------------------------


async def test_the_run_uses_its_own_factory_session_id():
    """TAŞIYICI PİM (spec §5): `factory-<şablon>-<örnek>`. Kadir'in sohbet
    oturumu bu koşudan HİÇ haberdar olmaz.

    ÖLDÜREN MUTASYON: `run_specialist`e Kadir'in oturum kimliğini geçirmek
    (ör. tool_context'ten okunan session.id) -> kimlik eşleşmez."""
    runner = FakeRunner([FakeEvent("bitti")])

    out = await _spawn(_template(name="arsivci"), runner=runner, instance="9f9f")

    assert runner.calls[0]["session_id"] == "factory-arsivci-9f9f"
    assert runner.calls[0]["user_id"] == USER
    assert out["ornek_id"] == "9f9f"
    assert out["sablon"] == "arsivci"


async def test_the_goal_is_what_the_specialist_is_asked_to_do():
    runner = FakeRunner([FakeEvent("bitti")])
    await _spawn(_template(), runner=runner, goal="repo özetini çıkar")
    assert runner.calls[0]["new_message"].parts[0].text == "repo özetini çıkar"


def test_two_instances_of_one_template_do_not_share_an_identity():
    a, b = factory.new_instance_id(), factory.new_instance_id()
    assert a != b
    assert factory.session_id_for("arastirmaci", a) != factory.session_id_for("arastirmaci", b)


# ---------------------------------------------------------------------------
# DEĞİŞMEZ 7 (spec §4): bilinmeyen şablon -> Türkçe gözlem, FIRLATMAZ
# ---------------------------------------------------------------------------


async def test_an_unknown_template_returns_an_observation_with_the_menu():
    out = await factory.spawn("uzayci", "hedef", user_id=USER, audit=FakeAudit(),
                              model=MODEL)

    assert out["durum"] == factory.STATUS_ERROR
    assert "uzayci" in out["hata"]
    adlar = [t["ad"] for t in out["kullanilabilir_sablonlar"]]
    assert adlar == sorted(factory.TEMPLATES)
    assert all(t["amac"] for t in out["kullanilabilir_sablonlar"])


@pytest.mark.parametrize("bad", ["", "   ", None, 42])
async def test_a_junk_template_argument_does_not_raise(bad):
    out = await factory.spawn(bad, "hedef", user_id=USER, audit=FakeAudit(), model=MODEL)
    assert out["durum"] == factory.STATUS_ERROR
    assert out["kullanilabilir_sablonlar"]


# ---------------------------------------------------------------------------
# Şablonlar (spec §3) ve çağrı yüzeyi (§4)
# ---------------------------------------------------------------------------


def test_the_three_shipped_templates_exist_with_the_spec_tool_sets():
    """Kademe 1'in TANIMI: şablon sayısı derleme anında sabittir. Yeni şablon
    eklemek bir KOD değişikliğidir — bu test o kararı görünür kılar."""
    assert sorted(factory.TEMPLATES) == ["arastirmaci", "arsivci", "nobetci"]
    assert set(factory.TEMPLATES["arastirmaci"].tools) == {
        "search_memory", "get_user_profile", "list_watched_repos", "get_repo_updates"}
    assert set(factory.TEMPLATES["arsivci"].tools) == {
        "search_memory", "remember_fact", "add_lesson", "update_user_profile"}
    assert set(factory.TEMPLATES["nobetci"].tools) == {
        "check_my_vitals", "list_reminders", "list_watched_repos"}


def test_no_shipped_template_may_carry_a_red_tool():
    """§8.5 değişmez 1'in şablon düzlemindeki izdüşümü: kırmızı bir araç bir
    şablona yazılırsa politika onu zaten engeller — ama şablonun onu İSTEMESİ
    bir tasarım hatasıdır ve burada yakalanır."""
    for tpl in factory.TEMPLATES.values():
        for name in tpl.tools:
            assert policy.check_zone(name) != config.ZONE_RED, f"{tpl.name}/{name}"


def test_every_template_carries_both_caps():
    for tpl in factory.TEMPLATES.values():
        assert tpl.max_steps > 0, tpl.name
        assert tpl.ttl_seconds > 0, tpl.name


def test_spawn_specialist_is_yellow():
    """spec §4: sarı bölge ("yap + bildir"). Kırmızı olsaydı her çağrı onay
    kartına düşer, yeşil olsaydı audit'te "bildir" yarısı zayıflardı."""
    assert config.TOOL_ZONES[factory.SPAWN_TOOL_NAME] == config.ZONE_YELLOW


async def test_the_tool_hands_the_session_user_to_the_factory(monkeypatch):
    """`spawn_specialist` aracı üretim yolunu bağlar: tool_context'ten kullanıcı,
    modül tekilinden db/audit. Gerçek LLM yok — factory.spawn sahteleniyor."""
    from app import memory as memory_mod

    db = FakeDB()
    tools_mod.init(memory_mod.Memory(db))
    gorulen = {}

    async def fake_spawn(template, goal, **kw):
        gorulen.update({"template": template, "goal": goal, **kw})
        return {"durum": factory.STATUS_OK}

    monkeypatch.setattr(factory, "spawn", fake_spawn)
    ctx = SimpleNamespace(session=SimpleNamespace(user_id=USER, id="chat-42"))

    out = await tools_mod.spawn_specialist("arastirmaci", "hedef", ctx)

    assert out == {"durum": factory.STATUS_OK}
    assert (gorulen["template"], gorulen["goal"]) == ("arastirmaci", "hedef")
    assert gorulen["user_id"] == USER
    assert gorulen["audit"].db is db


async def test_the_tool_reports_a_broken_wiring_as_an_observation(monkeypatch):
    """İlke 4 uçta da geçerli: tool_context'in şekli beklenmedikse araç
    FIRLATMAZ, Türkçe gözlem döner (ADK turu ölmez)."""
    out = await tools_mod.spawn_specialist("arastirmaci", "hedef", SimpleNamespace())
    assert "hata" in out


def test_the_orchestrator_is_told_when_to_use_the_factory():
    """Araç var ama talimat yoksa model onu hiç çağırmaz — son mil pimi."""
    from app.agent import INSTRUCTION

    assert factory.SPAWN_TOOL_NAME in INSTRUCTION
