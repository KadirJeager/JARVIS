# Tasarım — Faz Y4.1: Araç Kayıt Defteri + Araç Kazanım Merdiveni

**Tarih:** 3 Ağustos 2026
**North Star:** §8.5 (Kademeli Ajan Fabrikası → "Araç kazanım merdiveni"), §9 (Eylem Yetki Matrisi), §12 (Y4 satırı)
**Önkoşul:** Faz Y3 Onay Merkezi (`app/approvals.py`) — bu dilim onun `kind` alanını `tool_grant` ile kullanır.

## 1. Problem

§8.5: *"Jarvis, bir görev için eksik yeteneği kendisi tespit eder ve kurulumunu kendisi
hazırlar ('GitHub erişimi gerekiyor; şu MCP aracını, şu scope'larla ekleyeceğim') — öneri
onay merkezine düşer, Kadir'in tek tık onayıyla araç kayıt defterine girer ve matriste
bölgesi atanır. Keşif ve kurulum otonomdur; **yetkilendirme her zaman insanlıdır**."*

Bugün araç kümesi derleme anında sabit: `tools.ALL_TOOLS` bir Python listesi,
`config.TOOL_ZONES` bir sözlük. Jarvis'in yeni bir yetenek kazanmasının tek yolu bir
insanın kod yazıp deploy etmesidir. İkame ufku (§1) böyle genişleyemez.

## 2. Kapsam

**Bu dilim:** araç kayıt defteri (Firestore), `propose_tool` aracı, `tool_grant` onayı,
onaydan sonra kayıt, bölge çözümünün kayıt defterini de okuması, ve **onaylı MCP
sunucularının ajana gerçekten bağlanması** (yalnız yetki tablosu oynatmak değil — o
yarım bir özellik olurdu).

**Bu dilim DEĞİL:** fabrika Kademe 1/2 (§8.5 ajan düzlemi — ayrı dilim), araç
kaldırma UI'ı, çok kullanıcılı sahiplik.

## 3. Veri modeli — `tool_registry` koleksiyonu

Doküman kimliği = araç/sunucu adı (tekil isim uzayı; çakışma `create()` ile yakalanır).

| Alan | Tür | Anlam |
|---|---|---|
| `name` | str | Araç adı (builtin) veya MCP sunucu adı |
| `kind` | str | `builtin` \| `mcp` |
| `zone` | str | `green` \| `yellow` \| `red` — matristeki yeri (§9) |
| `status` | str | `granted` \| `revoked` |
| `why` | str | Jarvis'in gerekçesi (Türkçe) — Kadir kartta bunu okur |
| `mcp` | dict \| None | `kind="mcp"` için: `{transport, url\|command, args, scopes}` |
| `approval_id` | str | Bu kaydı doğuran onay — izlenebilirlik (§8.5 değişmez 5) |
| `granted_at` / `revoked_at` | str | ISO UTC |

## 4. Değişmezler

1. **Yetkilendirme insanidir.** `propose_tool` kayıt defterine **yazmaz**; yalnızca
   `kind="tool_grant"` bir onay kurar. Kayıt, `approvals.decide(..., "approved")`
   yürütücüsünde olur. Kod yolu başka türlü kayıt yazamaz (yazan tek fonksiyon
   yürütücüdür).
2. **Bölge çözümü daha gevşek olamaz.** `policy.check_zone` sırası: kayıt defteri →
   `config.TOOL_ZONES` → `DEFAULT_ZONE` (red). Ama kayıt defteri, koda gömülü bir
   zone'u **gevşetemez**: `config.TOOL_ZONES`'ta bir araç varsa kod kazanır. Kayıt
   defteri yalnızca kodun *bilmediği* araçlara bölge atar. Aksi hâlde onaylanmış tek
   bir kayıt, `cancel_reminder`'ı yeşile çekip onay merkezini baypas edebilirdi.
3. **Kırmızı bölge önerisi reddedilir.** `propose_tool` yalnızca `green`/`yellow`
   isteyebilir (§8.5 değişmez 1: üretilmiş yetenek misafir muamelesi görür,
   kırmızıya asla). Kırmızı bir yetenek hâlâ insan eliyle koda girer.
4. **Revoke geri alınabilirliktir.** `status="revoked"` kaydı bırakır, silmez — audit
   izi (§8.5 değişmez 5) korunur ve aynı ad yeniden önerilebilir.
5. **MCP bağlanması süreç ömrüyle sınırlıdır.** Onay anında kayıt yazılır; ajana
   bağlanma bir sonraki runner kurulumunda (soğuk başlangıç veya açık yeniden kurulum)
   olur. Çalışan bir ADK ajanının araç listesini yerinde değiştirmek desteklenmiyor —
   bunu **iddia etmiyoruz**, karta yazıyoruz: *"bir sonraki açılışta etkin olacak"*.

## 5. `propose_tool` aracı (yeşil bölge)

```
propose_tool(name, kind, zone, why, mcp_url=None, mcp_command=None, scopes=None) -> str
```

Yeşildir: bir öneri kurmak zararsızdır — asıl karar onay kartındadır. Doğrulama:
`kind` ∈ {builtin, mcp}, `zone` ∈ {green, yellow}, `name` boş değil ve zaten
kayıtlı/önerili değil (aynı ad için ikinci kart üretme). MCP için en az bir ulaşım
(url veya command) zorunlu.

## 6. MCP eklentisi

`app/tool_registry.py: mcp_toolsets(db) -> list[MCPToolset]` — `status="granted"` ve
`kind="mcp"` kayıtlardan ADK `MCPToolset` nesneleri kurar. `agent.build_agent` bunları
`tools=ALL_TOOLS + toolsets` olarak ekler.

**Hata izolasyonu:** bir MCP sunucusu ayağa kalkmazsa (yanlış URL, ölü süreç) **ajan
yine kurulmalıdır** — o toolset atlanır ve loglanır. Aksi hâlde tek bozuk kayıt
Jarvis'i tamamen sessizleştirir.

**Bölge:** MCP sunucusundan gelen araç adları önceden bilinmez. Bu yüzden bir MCP
kaydının zone'u, o sunucunun **tüm** araçlarına uygulanır: `check_zone` bilinmeyen bir
adı çözerken önce `tool_registry`'deki MCP kayıtlarının ön-ek/eşleme tablosuna bakar.
Bilinmiyorsa `DEFAULT_ZONE` (red) — güvenli taraf.

## 7. Kapsam dışı riskler (açıkça yazılı)

- MCP sunucusu Jarvis'in süreci içinde çalışır; kötü niyetli bir sunucu, verildiği
  bölgede ne yapabiliyorsa yapabilir. Bu yüzden kırmızı asla verilmez ve `why` kartta
  Kadir'e gösterilir.
- `scopes` alanı bu dilimde **belgeleyicidir** (karta yazılır), zorlayıcı değil —
  OAuth scope zorlaması MCP sunucusunun kendi işidir.
