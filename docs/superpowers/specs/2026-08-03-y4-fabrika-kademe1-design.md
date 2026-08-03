# Tasarım — Faz Y4.2: Ajan Fabrikası Kademe 1 (Kalıphane)

**Tarih:** 3 Ağustos 2026
**North Star:** §8.5 (Kademeli Ajan Fabrikası), §9 (Eylem Yetki Matrisi), §12 (Y4)
**Önkoşul:** Y4.1 araç kayıt defteri (`app/tool_registry.py`) — türetilmiş ajan araçlarını
yalnızca oradan ve `tools.ALL_TOOLS`'tan seçer.

## 1. Varlık sebebi

§8.5: *"Kadir'in yaptığı her iş türüne derleme anında statik bir ajan atanamaz. Statik
çekirdek (Kademe 0) en sık işleri taşır; kuyruğun sonsuz çeşitliliğini, şablondan
parametreyle örneklenen geçici ajanlar (Kademe 1) karşılar."*

Kademe 1'in tanımı: **şablon sayısı derleme anında sabittir**, örnek çalışma anında
parametreyle üretilir (araç alt kümesi + görev + bütçe + TTL). Debug yüzeyi bu yüzden
statik mimariye yakın kalır — sonsuz sayıda ajan değil, sonlu sayıda kalıp vardır.

## 2. Fabrika anayasası (§8.5 değişmezleri — hepsi testle pinlenir)

1. **Misafir muamelesi.** Türetilmiş ajan yeşil + sarı ile başlar, **kırmızıya asla**.
   Somut sonucu: türetilmiş ajanın politika callback'ine **`approval_sink` VERİLMEZ**.
   Kırmızı bir araç çağrısı onu onay kartına bile çeviremez; düz engellenir. Bölge
   terfisi yalnızca Kadir'in onayıyla ve yalnız kalıcı araç düzleminde olur (Y4.1).
2. **Yalnızca kayıt defterinden araç.** Örnek, şablonun izin verdiği ADLARIN
   `tools.ALL_TOOLS` ∩ (kayıt defterinde granted) kesişimini alır. Yeni araç veya kod
   üretemez. Şablonda yazan ama bulunamayan ad **sessizce atlanmaz** — loglanır.
3. **Ajan üretemez** (recursion yasağı). Türetilmiş ajanın araç kümesinde
   `spawn_specialist` **asla** bulunmaz; bu bir filtre değil, mutlak bir dışlamadır.
4. **TTL'lidir.** Örnek tek bir çağrının ömrü kadar yaşar ve iki bağımsız tavanla
   sınırlıdır: adım sayısı ve duvar saati. İkisinden biri dolduğunda durur ve
   Kadir'e ne yaptığını raporlar.
5. **Bütçe ve iz zorunludur.** Her araç çağrısı audit'e
   `actor="factory:<şablon>#<örnek>"` ile yazılır — "kim üretti, hangi kalıptan, hangi
   talimatla" §8.5'in açık şartı.
6. **Retro envanteri tarar.** Haftalık retro (§8.4) fabrika koşularını da sayar.

## 3. Şablonlar (derleme anında sabit)

`app/factory.py: TEMPLATES: dict[str, AgentTemplate]`

```
AgentTemplate(name, purpose, instruction, tools: tuple[str, ...],
              max_steps: int, ttl_seconds: int)
```

Bu dilimin şablonları — hepsi bugün mevcut araçlarla çalışır, hayali yetenek yok:

| Şablon | Amacı | Araçları |
|---|---|---|
| `arastirmaci` | Bir konuyu hafızadan + izlenen repo'lardan toplayıp özetler | `search_memory`, `get_user_profile`, `list_watched_repos`, `get_repo_updates` |
| `arsivci` | Bir konuşmadan çıkan kalıcı bilgiyi profile/derslere işler | `search_memory`, `remember_fact`, `add_lesson`, `update_user_profile` |
| `nobetci` | Kendi sağlığını ve bekleyen işleri derleyip rapor eder | `check_my_vitals`, `list_reminders`, `list_watched_repos` |

Yeni şablon eklemek **kod değişikliğidir** — Kademe 1'in tanımı budur.

## 4. Çağrı yüzeyi

`spawn_specialist(template: str, goal: str) -> dict` — **sarı bölge** ("yap + bildir":
çalışır ve Kadir audit'te/raporda görür; ne kalıcı bir şey yazar ne de kırmızıya
dokunabilir).

Dönen: `{sablon, ornek_id, adim, sonuc, durum}` — `durum` ∈
`tamam | adim_butcesi_doldu | sure_doldu | hata`.

Bilinmeyen şablon adı → Türkçe gözlem + kullanılabilir şablon listesi (İlke 4).

## 5. Yürütme

Örnek, Kademe 0 ile **aynı** ADK makinesinde koşar: `build_agent`'ın kullandığı model
fabrikası, `make_policy_callback` (sink'siz), ayrı bir `Runner` ve **ayrı bir oturum
kimliği** (`factory-<şablon>-<örnek>`), böylece Kadir'in sohbet geçmişi kirlenmez.

Adım tavanı: `Runner.run_async`'ten gelen olaylar sayılır; tavana varılınca döngü
kırılır. Süre tavanı: her olaydan sonra duvar saati kontrol edilir. **İkisi de
kontrol edilir** — tek bir araç çağrısı dakikalarca sürebilir (adım tavanı onu
yakalamaz), ve hızlı bir döngü saniyeler içinde yüzlerce adım atabilir (süre tavanı
onu yakalamaz).

## 6. Kapsam dışı (bilinçli)

- **Kademe 2** (onaylı kalıcı ajan üretimi): Y3'ün onay merkezi hazır ve `kind`
  alanı `agent_spec`'e açık, ama bu ayrı bir dilim.
- Tarif → ajan terfisi (§8.3'ün son maddesi).
- Örnekler arası paralellik: bu dilimde bir çağrı = bir örnek, sıralı.
