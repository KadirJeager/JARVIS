# Plan — Faz Y4.1: Araç Kayıt Defteri + Araç Kazanım Merdiveni

**Tarih:** 3 Ağustos 2026
**Spec:** `docs/superpowers/specs/2026-08-03-y4-arac-kazanim-design.md` (onaylı)
**Dal:** `feat/y3-onay-merkezi` üzerinde devam (Y3 önkoşul, aynı dalda)
**Test ortamı:** `cd brain && .venv/bin/python -m pytest -q`
**Taban:** 570 passed, 2 skipped

**DEPLOY YASAK:** bulutta başka ajanlar çalışıyor olabilir. Kod + yerel test.

---

## Görev 1 — `brain/app/tool_registry.py` (TDD)

1. **KIRMIZI:** `brain/tests/test_tool_registry.py` — `FakeDB` ile:
   - `grant()` kayıt yazar (`status=granted`, `granted_at`, `approval_id`)
   - aynı ad ikinci kez → `create()` ile `AlreadyExists` → Türkçe gözlem, üzerine YAZMAZ
   - `revoke()` `status=revoked` + `revoked_at`; kaydı SİLMEZ
   - `list_granted()` yalnız `granted` olanları döner
   - `zone_for(name)` → kayıtlı ve granted ise onun zone'u; revoked ise `None`;
     yoksa `None`
   - **GEVŞETME PİMİ:** `config.TOOL_ZONES`'ta VAR OLAN bir ad kayıt defterinde
     `green` olarak kayıtlıysa bile `policy.check_zone` **koddaki** zone'u döndürür
     (spec §4.2 — bu testin silinmesi onay merkezini baypas edilebilir yapar)
2. **YEŞİL:** `app/tool_registry.py` — `COLLECTION="tool_registry"`, `grant`,
   `revoke`, `list_granted`, `zone_for`, `get`.
3. `policy.check_zone` kayıt defterini okuyacak şekilde genişletilir. **Dikkat:**
   `check_zone` bugün saf ve db'siz; db'yi opsiyonel bir çözücü (`zone_resolver`)
   olarak enjekte et, `guest_gate` de aynı çözücüyü kullansın. Çözücü yoksa bugünkü
   davranış birebir korunur (regresyon pimi yaz).
4. Commit: `feat(tools): tool registry — granted capabilities are data, not code`

## Görev 2 — `propose_tool` + `tool_grant` yürütücüsü (TDD)

1. **KIRMIZI:** `brain/tests/test_tool_proposals.py`:
   - `propose_tool` bir onay kurar, kayıt defterine **YAZMAZ** (spec §4.1 pimi)
   - `zone="red"` önerisi REDDEDİLİR (Türkçe gözlem, onay kurulmaz — §4.3)
   - geçersiz `kind`, boş `name`, MCP'de ulaşım yokluğu → Türkçe gözlem
   - zaten kayıtlı/bekleyen aynı ad → ikinci kart ÜRETİLMEZ
   - `tool_grant` onayı onaylanınca kayıt defterine yazılır; reddedilince YAZILMAZ
   - onaydan sonra dönen `outcome` metni "bir sonraki açılışta etkin olacak" der (§4.5)
2. **YEŞİL:** `tools.propose_tool` (yeşil bölge, `TOOL_ZONES`'a ekle),
   `approvals.register_executor("tool_grant_...", ...)` — Y3'ün yürütücü kayıt
   defterini kullan; `kind="tool_grant"` onayları için yürütücü seçimi
   `approvals.decide` içinde `kind`'a göre yapılır (bugün yalnız `tool_call` var).
3. `agent.py` INSTRUCTION: bir görev için eksik yetenek fark edersen `propose_tool`
   ile öner, gerekçeni Türkçe yaz, sonra BEKLE — kendi kendine kurmaya çalışma.
4. Commit: `feat(tools): the acquisition ladder — Jarvis proposes, Kadir grants`

## Görev 3 — MCP eklentisi (TDD)

1. **ÖNCE DOĞRULA, SONRA YAZ:** kurulu `google-adk` 1.36.2'de `MCPToolset` ve
   `StdioConnectionParams`/`SseConnectionParams`/`StreamableHTTPConnectionParams`
   gerçek imzalarını **kaynaktan** oku (`.venv/lib/python3.14/site-packages/google/adk/
   tools/mcp_tool/`). Docstring'e hangi imzayı gördüğünü yaz. Tahmin etme.
2. **KIRMIZI:** `mcp_toolsets(db)`:
   - granted+mcp kayıtlardan toolset kurar
   - revoked / builtin kayıtları atlar
   - **bir kaydın toolset kurulumu fırlatırsa o kayıt atlanır, diğerleri kurulur ve
     fonksiyon FIRLATMAZ** (spec §6 — tek bozuk kayıt Jarvis'i susturmamalı)
   - `build_agent` toolset'leri `ALL_TOOLS`'un yanına ekler
3. **YEŞİL:** `tool_registry.mcp_toolsets`, `agent.build_agent(..., extra_toolsets=...)`,
   `main._init`/`_init_voice` bağlantısı.
4. Commit: `feat(tools): granted MCP servers actually attach to the agent`

---

## En olası üç kırılma noktası

1. **Kayıt defterinin koddaki bölgeyi gevşetmesi.** Görev 1'in gevşetme pimi bunun
   içindir; silinirse onaylanmış tek bir kayıt kırmızı bir aracı yeşile çekebilir.
2. **Bozuk bir MCP kaydının ajanı komple düşürmesi.** Görev 3'ün izolasyon testi.
3. **`propose_tool`'un kayıt defterine doğrudan yazması.** §4.1'in tek cümlelik
   garantisi: kayıt defterine YAZAN TEK YER yürütücüdür.
