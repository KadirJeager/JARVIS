"""tools._off_loop — senkron araçların event loop DIŞINDA koşması (§8.5 dilimi).

Neyi pinler: ADK 1.36.2 senkron bir araç fonksiyonunu event loop ÜZERİNDE satır
içi çağırır (google/adk/tools/function_tool.py `_invoke_callable`: senkron dal
`return target(**args_to_call)`), async olanı ise await eder. `_off_loop`
senkron araçları `asyncio.to_thread`e taşıyan async sarmalayıcıdır ve YALNIZCA
`ALL_TOOLS` listesinde uygulanır: modül seviyesindeki isimler senkron kalır,
çünkü guest_gate onları kendi `asyncio.to_thread`üyle doğrudan çağırıyor —
yerinde async yapmak orayı kırardı.

Sarmalayıcının sözleşmesi üç sınırda ölçülür:

1. ADK sınırı: bildirim şeması (`_get_declaration`) ve `tool_context` geçişi
   sarmalanmamış hâliyle BİREBİR aynı kalmalı — ikisi de `inspect.signature`
   üzerinden türetilir ve `functools.wraps` `__wrapped__` ile onu korur.
2. Loop sınırı: bloklayan bir araç koşarken loop nefes almaya devam etmeli,
   ve bekleyiş `asyncio.timeout` ile kesilebilir olmalı. (Kesilen şey BEKLEYİŞ;
   to_thread iptal EDİLEMEZ, iş parçacığı arka planda sonuna kadar koşar.)
3. Kimlik sınırı: zaten async olan araç (spawn_specialist) OLDUĞU GİBİ geçer —
   test_factory kimliğini `in ALL_TOOLS` ile pinliyor.
"""
import asyncio
import inspect
import threading
import time
from types import SimpleNamespace

import pytest
from google.adk.tools import FunctionTool

from app import tools


# ---------------------------------------------------------------------------
# Kimlik sınırı: ALL_TOOLS'un tamamı ADK'nın await edeceği biçimde
# ---------------------------------------------------------------------------


def test_every_all_tools_entry_is_a_coroutine_function():
    """TAŞIYICI PİM: listede TEK BİR senkron araç kalsa, ADK onu loop üzerinde
    satır içi çağırır ve bu dilimin tüm garantisi o araç için sessizce yok olur.

    ÖLDÜREN MUTASYON: ALL_TOOLS'taki `_off_loop(...)` sarmasını kaldırmak."""
    kacaklar = [getattr(f, "__name__", repr(f)) for f in tools.ALL_TOOLS
                if not inspect.iscoroutinefunction(f)]
    assert kacaklar == [], f"loop üzerinde satır içi çağrılacak araçlar: {kacaklar}"


def test_spawn_specialist_passes_through_with_its_identity():
    """Zaten async olan araç sarmalanmaz: test_factory recursion yasağını
    `tools.spawn_specialist in ALL_TOOLS` kimliğiyle pinliyor; bir sarmalayıcı
    o pini sessizce anlamsızlaştırırdı."""
    assert tools._off_loop(tools.spawn_specialist) is tools.spawn_specialist
    assert tools.spawn_specialist in tools.ALL_TOOLS


# ---------------------------------------------------------------------------
# Mekanizma: thread'e taşıma, değer/hata geçişi
# ---------------------------------------------------------------------------


async def test_a_wrapped_tool_runs_off_the_event_loop_and_returns_its_value():
    gorulen = {}

    def sync_tool() -> str:
        gorulen["thread"] = threading.current_thread()
        return "sonuç"

    out = await tools._off_loop(sync_tool)()

    assert out == "sonuç"
    assert gorulen["thread"] is not threading.main_thread()


async def test_a_tool_error_still_propagates_through_the_wrapper():
    """İlke 4 araç SEVİYESİNDE uygulanır (araçlar gözlem döner); sarmalayıcı o
    katmanın altındadır ve hatayı YUTMAMALIDIR — yutan bir sarmalayıcı her
    aracın hata sözleşmesini sessizce değiştirirdi."""

    def kirik_tool():
        raise ValueError("beklenen")

    with pytest.raises(ValueError, match="beklenen"):
        await tools._off_loop(kirik_tool)()


# ---------------------------------------------------------------------------
# ADK sınırı: şema ve tool_context sarmalamadan etkilenmez
# ---------------------------------------------------------------------------


def test_adk_declaration_is_identical_for_wrapped_and_original():
    """Modelin gördüğü araç tanımı sarmalamadan HİÇ etkilenmemeli: ad, açıklama,
    parametre şeması. İkisi de gerçek bir üretim aracından (cancel_reminder)
    türetilir ki karşılaştırma temsili değil gerçek olsun.

    ÖLDÜREN MUTASYON: sarmalayıcıdan `functools.wraps`ı kaldırmak -> ad
    "off_loop" olur, parametreler kaybolur ve model aracı çağıramaz hâle
    gelir."""
    orig = FunctionTool(tools.cancel_reminder)._get_declaration()
    sarili = FunctionTool(tools._off_loop(tools.cancel_reminder))._get_declaration()

    assert sarili.model_dump() == orig.model_dump()


async def test_adk_passes_tool_context_through_the_wrapper():
    """ADK, `tool_context`i imzada GÖREREK enjekte eder (function_tool.py
    `inspect.signature(self.func)`); sarmalayıcı imzayı gizlerse enjeksiyon
    sessizce kaybolur ve araç kimliksiz koşar — testin var olma sebebi bu
    sessizliktir."""
    gorulen = {}

    def sync_tool(soru: str, tool_context) -> str:
        gorulen["ctx"] = tool_context
        return f"cevap:{soru}"

    ctx = SimpleNamespace(session=SimpleNamespace(user_id="kadir@example.com"))
    out = await FunctionTool(tools._off_loop(sync_tool)).run_async(
        args={"soru": "selam"}, tool_context=ctx)

    assert out == "cevap:selam"
    assert gorulen["ctx"] is ctx


# ---------------------------------------------------------------------------
# Loop sınırı: bloklayan araç loop'u durdurmaz ve bekleyiş kesilebilir
# ---------------------------------------------------------------------------


async def test_the_event_loop_stays_live_while_a_wrapped_tool_blocks():
    """Dilimin varlık sebebi: ölçülmüş hâlde 3.0 sn'lik senkron bir çağrı
    loop'un tamamını (sohbet, ses WS, misafir kapısı) bloke ediyordu. Sarılı
    araç koşarken loop'ta kalp atışı devam etmeli.

    ÖLDÜREN MUTASYON: sarmalayıcıdaki `asyncio.to_thread`i doğrudan `fn(...)`
    çağrısına çevirmek -> kalp atışı 0 kalır."""
    atis = {"n": 0}

    async def kalp():
        while True:
            atis["n"] += 1
            await asyncio.sleep(0.01)

    def blocking_tool():
        time.sleep(0.3)
        return "bitti"

    heartbeat = asyncio.ensure_future(kalp())
    try:
        out = await tools._off_loop(blocking_tool)()
    finally:
        heartbeat.cancel()

    assert out == "bitti"
    # 0.3 sn'de ~30 atış beklenir; bloklansaydı 0-1 olurdu. Eşik bilerek
    # muhafazakâr: yavaş bir CI makinesi testi yanlış kırmasın.
    assert atis["n"] >= 5, f"loop bloklandı: {atis['n']} atış"


async def test_a_blocking_tool_wait_is_bounded_by_asyncio_timeout():
    """factory.run_specialist'in ölçülmüş sınırıydı: ttl=0.2 sn iken 3.0 sn
    bloklayan araç 3.0 sn sürüyordu. Sarılı araçta bekleme bir `await`
    noktasıdır ve `asyncio.timeout` onu keser. Kesilen BEKLEYİŞtir: iş
    parçacığı arka planda biter (to_thread iptal edilemez), yani TTL "en fazla
    bu kadar beklersin" garantisine döner, "araç yan etkisiz kalır" DEĞİL."""

    def blocking_tool():
        time.sleep(0.6)

    basladi = time.monotonic()
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await tools._off_loop(blocking_tool)()
    gecen = time.monotonic() - basladi

    assert gecen < 0.5, f"bekleyiş kesilmedi: {gecen:.3f} sn"
