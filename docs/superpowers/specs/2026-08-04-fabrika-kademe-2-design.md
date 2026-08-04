# Fabrika Kademe 2 — Onaylı Fabrika (North Star §8.5, Faz Y4 kuyruğu)

Tarih: 2026-08-04 · Durum: Kadir onayladı (sohbette, 03:30) · Kapsam: yalnız `brain/`

## 1. Amaç ve bağlam

Kademe 1 (Kalıphane) canlı: derleme anında sabit `TEMPLATES`, çalışma anında
parametreyle örneklenen GEÇİCİ uzman ajanlar. Kademe 2 bunun üstüne **kalıcılaşma
yolunu** ekler: orkestratör yeni bir ajan tanımı önerir, öneri onay merkezine kart
olarak düşer, Kadir onaylarsa tanım **kayıt defterine kalıcı şablon olarak yazılır**.

North Star'ın iki cümlesi tasarımın anayasasıdır:

> "Üretim çalışma anında değil, **onay anında** gerçekleşir — runtime hep statik
> ajanlarla döner, ama ajan kümesi evrimleşir." (§8.5 Kademe 2)

> "TTL'lidir: iş bitince ölür; **kalıcılaşmak Kademe 2 onayından geçmek demektir**."
> (§8.5 değişmez 4)

Yani Kademe 2'de kalıcılaşan şey ÖRNEK değil ŞABLONDUR. Kayıtlı bir ajan da her
koşuda K1 örneğiyle aynı misafir makinesinde doğar, koşar ve ölür.

Model, araç kazanım merdiveninin kanıtlanmış deseninin ajan düzlemine izdüşümüdür:
`propose_tool → onay kartı → _execute_tool_grant → tool_registry.grant`
birebir `propose_agent → onay kartı → _execute_agent_grant → agent_registry.grant`
olur.

**Öneri kapısı (Kadir'in kararı, 03:28): YUMUŞAK.** `propose_agent` her an
çağrılabilir; sayısal eşik yok. `evidence` (kanıt) alanı zorunludur ve kartta
görünür; süzgeç Kadir'in onayıdır. Retro verisi karta kanıt olarak girebilir ama
ön şart değildir.

## 2. Kapsam ve bilinçli sınırlar

- **Yalnız sunucu (brain).** Onay kartları tür-bağımsız çizildiği için Android
  değişikliği yoktur; kart mevcut Onayla/Reddet akışıyla çalışır.
- **v1'de kayıtlı ajan yalnız BUILTIN araçlardan seçer** (`tools.ALL_TOOLS`
  adları). Granted-MCP araçları bilinçli olarak dışarıdadır: misafire
  `zone_resolver` verilmez (K1 değişmez 1), dolayısıyla kayıt-defteri araçları
  misafir yüzeyinde zaten kırmızıya düşer ve düz engellenir (`policy.check_zone`
  fail-closed sırası: kod → resolver → red). MCP'yi misafire açmak ayrı bir
  bölge-modeli kararıdır, bu dilime girmez.
- **Revoke ajan yüzeyine açılmaz** (araç tarafıyla parite: `tool_registry.revoke`
  da bugün yalnız idari/elle yoldur). `agent_registry.revoke` fonksiyon olarak
  yazılır ve test edilir.
- Kademe 3'e giden hiçbir şey yok: onaysız üretim yolu YOKTUR.

## 3. Veri modeli — `agent_registry` koleksiyonu

Doküman kimliği = ajan adı (tekil isim uzayı; `tool_registry` ile aynı desen).

```
{
  name:         str,        # doc id ile aynı
  purpose:      str,        # menüde görünen Türkçe amaç
  instruction:  str,        # sistem talimatı (SAKLANAN metin; _GUEST_RULES eklenmeden)
  tools:        [str, ...], # builtin araç adları
  max_steps:    int,        # tavan 1 (olay sayısı)
  ttl_seconds:  int,        # tavan 2 (duvar saati)
  status:       "granted" | "revoked",
  why:          str,        # Kadir'in kartta okuduğu gerekçe
  evidence:     str,        # yumuşak kapının kanıt alanı (hangi tekrar eden iş, kaç kez)
  approval_id:  str,        # kaydı doğuran onay — izlenebilirlik (§8.5 değişmez 5)
  granted_at:   str,        # ISO-8601 UTC
  revoked_at:   str | None,
}
```

`instruction` DEPOLANIRKEN `_GUEST_RULES` içermez; misafir kuralları şablon
kurulurken BUILDER tarafından eklenir (§6). Depolanan metne güvenilmez — veri
kanalından anayasa gevşetilemez.

## 4. `propose_agent` aracı (yeşil bölge)

İmza: `propose_agent(name, purpose, instruction, tools, why, evidence,
tool_context, max_steps=24, ttl_seconds=90)` — `tools` virgülle ayrılmış ad
listesi (araç şeması düz tipler ister; `propose_tool.scopes` ile aynı biçim).

Kendi başına HİÇBİR ŞEY kurmaz: yalnız onay kartı oluşturur (`propose_tool`
sözleşmesinin aynısı). FIRLATMAZ; her ret Türkçe gözlem döner (İlke 4).

Doğrulama — öneri anında VE grant anında AYNI kurallar (`agent_registry.validate`
tek kaynak; çift savunma `tool_registry.validate` gerekçesiyle):

1. `name` boş olamaz; `factory.TEMPLATES` içindeki bir adı GÖLGELEYEMEZ
   (derleme-anı şablon her zaman kazanır; çakışan öneri reddedilir).
2. `agent_registry`'de `granted` kayıt varsa reddedilir ("zaten kayıtlı");
   `revoked` kayıt yeniden önerilebilir (§7).
3. `tools` boş olamaz. Her ad: (a) `factory.SPAWN_TOOL_NAME` OLAMAZ (recursion
   yasağı öneri anında da reddedilir — K1'deki sessiz dışlamanın öneri düzlemi
   sıkı hâli), (b) mevcut `tools.ALL_TOOLS` kataloğunda OLMALI (bilinmeyen ad =
   yazım hatası; K1'in logla-ve-atla'sı spawn anına aittir, öneri anında ret),
   (c) `policy.check_zone(ad)` yeşil veya sarı çözmeli — kırmızı araç taşıyan
   öneri reddedilir (K1'in `test_no_shipped_template_may_carry_a_red_tool`
   pininin veri düzlemi karşılığı).
4. Tavanlar derleme-anı üst sınırların içinde olmalı:
   `max_steps ∈ [1, MAX_STEPS_CEILING=32]`, `ttl_seconds ∈ [1, TTL_CEILING=120]`.
   Gerekçe: K1'in en büyük sevkiyat şablonu 24/90; tavan değerleri henüz
   ölçülmediği için (bilinen borç) üst sınır muhafazakâr tutulur ve ölçüm
   geldiğinde tek sabit değişir. Varsayılanlar K1 `arastirmaci` ile aynıdır (24/90).
5. `purpose`, `instruction`, `why`, `evidence` boş olamaz (yumuşak kapının
   bedeli: kanıt metni zorunlu).
6. Bekleyen mükerrer kart kontrolü: `approvals.find_pending_duplicate(db,
   user_id, name, {}, kind=KIND_AGENT_GRANT)` — `propose_tool`'daki gerekçeyle
   `list_pending` DEĞİL (MAX_PENDING kesmesi mükerrer kontrolünde kör nokta).
7. Uzunluk tavanları — `approvals._normalize_args` her `tool_args` değerini
   500 karakterde keser ve yürütücü şablonu KARTIN `tool_args`ından kurar;
   işlevsel alanlar bu yüzden sınırlıdır: `instruction ≤ 500` karakter,
   `purpose ≤ 200`, araç sayısı ≤ 8 (virgüllü ad listesi 500'ü aşamaz).
   `why`/`evidence` belgeleyicidir; kesilmeleri kaydın işlevini bozmaz.
   Sınır aşımı öneri anında Türkçe retle döner.

Kart içeriği: başlık `"Kalıcı ajan '<name>' kurulsun mu?"`; detayda amaç,
**instruction'ın TAM METNİ** (kart onayı fiilen bir kod incelemesidir — Kadir
modele gidecek talimatı görmeden onaylamamalı), araç listesi (her birinin bölgesiyle),
tavanlar, gerekçe ve kanıt. `zone` alanı bilgi amaçlıdır: araç kümesinin en
yüksek bölgesi (sarı varsa `yellow`, yoksa `green`).

`approval_id` ÖNCEDEN üretilir ve `tool_args`a konur (`propose_tool` ile aynı
gerekçe: yürütücü sözleşmesi `(tool_args, user_id)` olduğundan izlenebilirlik
kimliği ancak böyle taşınır). `tool_args` şablon tanımının tamamını taşır.

## 5. Onay yürütücüsü — `approvals` genişletmesi

`approvals.py`'deki mevcut öngörü ("§8.5'in ileride gelecek `agent_spec`'i")
şimdi doldurulur, desen `tool_grant`'ın birebir aynısı:

- `KIND_AGENT_GRANT = "agent_grant"` — `EXECUTABLE_KINDS`e eklenir.
- `EXECUTOR_AGENT_GRANT = "kind:agent_grant"` — isim-uzaylı anahtar;
  `_executor_key` bu tür için onu döner. `kind:` ön eki reddi zaten genel.
- `tools.py` sonunda: `approvals.register_executor(EXECUTOR_AGENT_GRANT,
  _execute_agent_grant)`.

`_execute_agent_grant(tool_args, user_id)` → `agent_registry.grant(...)`.
**Kayıt defterine yazan TEK yol budur**; `propose_agent` yazamaz,
`agent_registry.grant` başka hiçbir yerden çağrılmaz (`_execute_tool_grant`
sözleşmesinin aynısı). Yürütücü, grant öncesi `validate`'i yeniden koşar
(öneriyle karar arasında kod değişmiş, araç kalkmış, bölgesi kaymış olabilir).

`agent_registry.grant` çakışma davranışı `tool_registry.grant` ile aynı:
atomik `create()`; `granted` mevcutsa DOKUNULMAZ; `revoked` mevcutsa yeniden
kazandırılır (`merge=True`, `revoked_at` temizlenir).

## 6. Fabrika entegrasyonu — kayıtlı şablonla spawn

`factory.spawn` şablon çözümü iki katmanlı olur:

1. `TEMPLATES` (derleme anı) — önce ve her zaman kazanır.
2. Yoksa `agent_registry`'den CANLI okuma: `granted` doküman varsa ondan
   `AgentTemplate` kurulur; yoksa/`revoked` ise `unknown_template_reply`.

Canlı okuma bilinçlidir ve araç tarafından FARKLIDIR: MCP toolset'leri açılışta
bağlanır ("bir sonraki açılışta etkin olur"), veri şablonuysa spawn anında tek
doküman okumasıdır — onaydan hemen sonra kullanılabilir ("üretim onay anında
gerçekleşir" cümlesinin operasyonel karşılığı). Firestore erişimi başarısızsa
FIRLATMAZ: Türkçe gözlemli `durum="hata"` döner.

Dokümandan şablon kurulurken:

- `instruction = saklanan_metin + _GUEST_RULES` — misafir kuralları BURADA
  eklenir, veriye güvenilmez.
- Doküman alanları yeniden doğrulanır (`validate`; bozuk/elle yazılmış doküman
  fail-closed reddedilir ve loglanır).
- `AgentTemplate(frozen=True)` aynı tip; sonrası K1 ile TEK yol: `resolve_tools`
  (SPAWN mutlak dışlanır + ALL_TOOLS kesişimi + eksik ad LOGLANIR),
  `build_specialist` (approval_sink YOK, zone_resolver YOK), çift tavan,
  ayrı oturum servisi, `actor="factory:<ad>#<örnek>"` audit izi.

`unknown_template_reply` menüsü kayıtlı ajanları da listeler (kaynağı belli
olsun diye `"kaynak": "kayit_defteri"` etiketiyle; derleme-anı şablonlar
`"kaynak": "sevkiyat"`). Menü sorgusu `status == granted` filtreli, alfabetik,
makul limitli (tek-alan sorgu; composite index gerekmez — `conversations` 502
vakasının sınıfına girmez).

`spawn_specialist` docstring'i güncellenir: kayıtlı ajanların da şablon adıyla
çağrılabildiğini ve bilinmeyen ad menüsünün iki kaynağı da listelediğini söyler.

## 7. Revoke ve retro

- `agent_registry.revoke(db, name)`: kayıt SİLİNMEZ, `revoked` damgalanır
  (audit izi okunur kalır); aynı ad yeniden önerilebilir (`grant`'ın revoked
  dalı). Ajan yüzeyine araç olarak AÇILMAZ.
- Haftalık retro DEĞİŞMEZ: kayıtlı ajan koşuları aynı `factory:<ad>#<örnek>`
  aktör biçimini kullandığı için mevcut şablon-başına koşu sayımı onları
  otomatik kapsar — §8.5 değişmez 6 ("retro envanteri tarar") ek kod olmadan
  sağlanır. Spec bunu test ile pinler (retro sayımında kayıt-defteri kaynaklı
  şablon adı görünür).

## 8. Test planı (TDD; FakeDB mevcut altyapı)

- **test_agent_registry:** validate retleri (boş ad, TEMPLATES gölgesi, boş/
  bilinmeyen/kırmızı/spawn araç, tavan aşımı, boş kanıt); grant atomikliği
  (granted'a dokunulmaz, revoked yeniden kazandırılır, revoked_at temizlenir);
  revoke damgası.
- **test_propose_agent:** kart kurulur ve `agent_registry`'ye HİÇBİR ŞEY
  yazılmaz (aynı isimli `test_propose_tool_writes_nothing_to_the_registry`
  pini); tüm validate retleri araçtan Türkçe gözlem olarak döner; mükerrer
  bekleyen kart ikinci kart üretmez; süresi geçmiş kart mükerrer sayılmaz;
  kartta instruction tam metni ve kanıt görünür; backend hatası Türkçe gözlem.
- **test yürütücü:** `decide(approved)` → `agent_registry`'de granted doküman;
  `_executor_key` `agent_grant` için isim-uzaylı anahtarı çözer; kendini
  `kind:agent_grant` diye adlandıran tool_call yürütücüyü ödünç alamaz;
  yürütücüdeki yeniden-doğrulama bozulmuş `tool_args`ı reddeder (failed).
- **test factory (veri şablonu):** kayıtlı ajan spawn edilir ve sonuç döner;
  instruction'a `_GUEST_RULES` EKLENMİŞTİR (depolanan metinde yokken);
  `TEMPLATES` adı registry'deki aynı adı GÖLGELER; revoked ajan spawn edilemez
  (menüye düşer); menüde iki kaynak etiketiyle listelenir; kayıtlı şablon
  spawn'ı `spawn_specialist`i araç kümesine ALAMAZ (recursion pini, veri
  düzleminde); çift tavan kayıtlı şablonun değerleriyle işler; bozuk doküman
  fail-closed reddedilir.
- **test retro:** kayıt-defteri kaynaklı bir `factory:` aktörü şablon-başına
  sayımda görünür.

Testler ÜRETİMİN GERÇEK OLAY DİZİSİYLE kurulur (3 Ağu dersi: tek olayla kurulan
test boş yeşildir) — yürütücü testi `decide`'ın tam yolundan geçer, kart testi
`approvals.request`'in yazdığı gerçek dokümanı okur.

## 9. Bu dilime özgü başarısızlık modları

1. **Instruction verisi modele gider.** Karşılık: kartta tam metin (onay = kod
   incelemesi), `_GUEST_RULES` builder'da zorlanır, misafir anayasası araç
   yüzeyini zaten sınırlar (politika engeli talimata bakmaz, araca bakar).
2. **Öneriyle karar arasında dünya değişir** (araç koddan kalkar, bölgesi
   kayar, ad TEMPLATES'e girer). Karşılık: yürütücü grant öncesi yeniden
   doğrular; spawn anında üçüncü savunma (K1 logla-ve-atla + doküman
   yeniden-doğrulaması).
3. **Spawn-anı Firestore bağımlılığı.** Karşılık: tek doküman get; hata
   FIRLATMAZ, Türkçe gözlem döner; derleme-anı şablonlar Firestore'suz
   çalışmaya devam eder.

## 10. Canlı kanıt ölçütü ("bitti" tanımı)

Testler yeşil YETMEZ (3 Ağu dersleri: tetikleyici kanıtı + hangi koddan geldiği).
Sırayla:

1. Deploy sonrası `/openapi.json` yol envanteri beklenen kümede (deploy doğrulama
   standardı) ve trafik YENİ revizyonda (`latestRevision: true` YAML'ları).
2. Gerçek sohbette `propose_agent` → S23'te kart görünür (instruction tam metniyle).
3. Onay → `agent_registry`'de `granted` doküman, `approval_id` kartla eşleşir.
4. O adla gerçek bir `spawn_specialist` koşusu sonuç döndürür ve audit'te
   `factory:<ad>#<örnek>` aktörü görünür.
5. Negatif kontrol: kırmızı araç isteyen bir öneri araçtan Türkçe retle döner
   ve HİÇBİR kart/doküman doğmaz.

## 11. Deploy

Tek servis (brain) + voice aynı imajdan gider (mevcut süreç). Firestore composite
index GEREKMEZ (tek-alan sorgular). Android APK değişikliği YOK — kartlar
tür-bağımsız. Yeni env/secret YOK.
