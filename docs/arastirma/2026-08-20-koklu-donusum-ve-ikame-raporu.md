# JARVIS: Geniş Rekabet Manzarası, Stratejik Konum ve Boşluk-Kapama Mimarisi

**Tarih:** 20 Ağustos 2026
**Revizyon:** 3.0 — derin araştırmaya dayalı tam yeniden yazım · **3.1 — 22 Ağustos 2026: durum düzeltmesi ve tek yol haritası**
**Girdi:** [2026-08-05 Ekosistem Analizi (189KB)](2026-08-05-ekosistem-analizi.md) + çevrimiçi güncel tarama (20 Ağu 2026) + **repo-düzeyinde durum doğrulaması (22 Ağu 2026)**

---

## 0. Bu Belge Ne ve Ne Değil

**Nedir:** JARVIS projesinin *"piyasada olanı yeniden üretme, sadece eksiklikleri kapa"* ilkesine göre şekillendirilmiş stratejik konumlanma belgesi. Tüm açık kaynak kişisel asistan projelerini, ajan çerçevelerini, orkestrasyon katmanlarını, hafıza framework'lerini, ses/güvenlik ekosistemlerini ve endüstri standartlarını tarıyor.

**Ne değildir:** Tek bir projeye (OpenClaw, Hermes, vb.) bağlı kalmış dar analiz değil. Her proje yalnızca *bir referans noktası*.

### 0.1 Rev 3.1 — neyi neden düzeltti (22 Ağu 2026)

3.0, 5 Ağustos anlık görüntüsüne yazılmıştı; aradan geçen iki haftanın işlerini görmüyordu ve kayıtlı kararlarla çelişiyordu. Repo-düzeyinde doğrulamayla yapılan düzeltmeler:

- **"Anti-spoofing yok" iddiası bayattı** (§2.1, §10): CM kapısı 6 Ağu'da canlıya alındı, galeriye yazan üç kapı 11 Ağu'da CM'e bağlandı. Kalan açık ölçüm/kalibrasyona daraldı (§1.6, §7).
- **`recipes.py` mevcut kod gibi listeleniyordu** (§4.2): dosya yok; tarif katmanı planlı bir iş.
- **PerTh filigranı "⭐ en ucuz/en yüksek getiri" diye listeleniyordu**: 6 Ağu Kadir kararıyla önceliği düşük-ortaya inmişti (§7 sonundaki kayıt).
- **UI'ya "devret" tavsiyesi**, 11 Ağu'daki "UI ana iş yüzeyidir" düzeltmesiyle ve mevcut istemci yatırımıyla çeliyordu (§8).
- **Yol haritası ikiye bölünmüştü**: tipli ret hem burada hem Onay Kartı 2.0 planında sayılıyor, hash-zincirli audit ikisinde de vardı; North Star §12 ile eşleme yoktu. §7 tek haritaya indirildi.
- **Ürün yüzeyi boşlukları eksikti**: 11 Ağu'da ölçülen üç boşluk (akış/SSE yok, sesli arama zaman çizgisi dışında, rapor oturumları uygulamada görünmüyor) haritaya eklendi (§7).
- Yıldız/Efor sayıları 5 Ağu analizinden kalır ve bugün tek tek yeniden doğrulanmadı; kaynak-düzeyi doğrulama oradaki belgededir.

---

## 1. Rekabet Manzarası: 2026 Ağustos Ekosistem Haritası

### 1.1 Kişisel Asistan Projeleri (Doğrudan Rakipler)

> Ayrıntılı kaynak-kod-düzeyinde analiz → [ekosistem-analizi.md §1–3](../arastirma/2026-08-05-ekosistem-analizi.md)

| Proje | ★ | Mimari | Mobil | Ses | Hafıza | Güvenlik/HITL | Scale-to-Zero |
|---|---|---|---|---|---|---|---|
| **OpenClaw** | ~200K | Plugin/MCP, yerel daemon | ✅ Android+iOS+WearOS | ✅ kapsamlı, speaker-id yok | Session+FTS5+plugin | Exec approval, bağlamdan kopuk | ❌ always-on |
| **Hermes Agent** | ~225K | Tek `AIAgent` sınıfı, always-on gateway | ❌ | ✅ wake word+STT+TTS, speaker-id yok | 3 kademe + 8 memory provider | Approval gate (varsayılan kapalı) | ❌ always-on |
| **QwenPaw** | ~33K | Docker/ECS, scale-to-zero yok | Web+TUI+Tauri | — | Scroll Context + ReMe KB | En kapsamlı: allow/deny/ask/sandbox | ❌ |
| **ZeroClaw** | ~33K | Rust tek binary | CLI+ACP | — | SQLite+embeddings | Kriptografik tool receipts | ❌ |
| **OpenHuman** | ~36K | Tauri masaüstü | ❌ | — | Memory Tree | Checkpoint graf, E2E şifre | ❌ |
| **Khoj** | ~36K | Django+Postgres | TWA (PWA) | — | Doküman RAG | Hesap/oturum; HITL yok | ❌ |
| **Leon** | ~17K | Node.js, Satellite deseni | ❌ | — | Persistent+daily+OWNER.md | Profil izolasyonu | ❌ |
| **Omi** | ~13K | FastAPI+Firebase | ✅ Flutter iOS+Android | Speech Profile (~90sn) | Konuşma hafızası | — | ❌ sürekli ses |
| **PicoClaw** | ~30K | Go tek binary | Android APK (ajan telefonda) | — | JSONL | Hook approval | ❌ |
| **Vellum** | ~1K | Bun+SQLite | Capacitor (zayıf) | — | 8 tip iddia, doğrulanamamış | Guardian JWT + credential broker | ❌ |

**Sonuç:** Kategorinin tamamı yerel-öncelikli veya always-on. **Cloud Run + scale-to-zero + ince istemci** yapan kimse yok.

### 1.2 Ajan Çerçeveleri ve Orkestratörler (Altyapı Katmanı)

| Çerçeve | Tip | Güçlü Yan | JARVIS İle İlişki |
|---|---|---|---|
| **Google ADK** | Hiyerarşik ajan | GCP-native, zero-trust, Vertex AI entegrasyonu | ✅ **Zaten kullanıyoruz** — çekirdek orkestratör |
| **LangGraph** | Durum makinesi/graf | Dayanıklı yürütme, checkpoint, HITL (timeout yok!) | Desen olarak ilham; bağımlılık olarak ADK ile çakışır |
| **CrewAI** | Rol tabanlı çoklu-ajan | Hızlı prototip, ekip metaforu | JARVIS'in uzman ajan modeline yakın; ADK zaten karşılıyor |
| **DeepSeek Harness (dsh)** | Plugin tabanlı runtime | "Everything is a plugin" (Cordis), model-agnostik, YAML config | 🆕 **13 Ağu 2026 duyuruldu**, developer preview. Modülerliği ilham verici; erken ve kırılgan |
| **AutoGen (AG2)** | Konuşma döngüsü | Dinamik, araştırma odaklı | Akademik; JARVIS'in ADK tabanlı mimarisine uymuyor |
| **OpenHands (eski OpenDevin)** | Otonom kodlama ajanı | SWE-bench %72, Docker sandbox, RBAC | Kodlama otomasyonu alt-ajanı olarak entegre edilebilir |
| **SWE-Agent** | Kodlama özelleşmiş | Repo düzeyinde hata çözme | Spesifik görev; genel asistan değil |
| **Cline / Continue.dev** | IDE uzantısı | VS Code/JetBrains içinde ajan | IDE-spesifik; JARVIS'in kapsamı dışında |
| **Aider** | CLI Git-native | Terminal tabanlı çift programlama | Geliştirici aracı; asistan değil |

**JARVIS için çıkarım:** ADK doğru çerçeve seçimi. DeepSeek Harness'ın plugin mimarisi ilginç ama 1 haftalık (developer preview). LangGraph'ın dayanıklı yürütme ve checkpoint desenleri ödünç alınabilir ama framework olarak benimsenmemeli (ADK ile ikinci çerçeve yükü orantısız).

### 1.3 Endüstri Standartları ve Protokoller (2026)

| Standart | Durum | JARVIS Etkisi |
|---|---|---|
| **MCP (Model Context Protocol)** | ✅ Olgun — Agentic AI Foundation (Linux Foundation), aylık ~500M indirme, Tem 2026'da stateless'a geçti | Araç bağlantısının standart yolu; JARVIS zaten MCP tüketiyor |
| **A2A (Agent-to-Agent Protocol)** | ⚠️ Büyüyor — 17 Ağu 2026'da AAIF'a taşındı, 150+ kuruluş | Misafir kapısı ve ajan fabrikası için gelecekteki interop katmanı |
| **OCSF / OWASP AOS** | ⚙️ v1.0.0 | Audit log şeması için isimlendirme referansı |
| **OpenTelemetry GenAI** | ⚠️ "Development" | Gözlemlenebilirlik için; güvenlik audit'i değil |
| **IETF Agent Identity (DAAP/OBO)** | ⚗️ RFC değil | Fabrikadan çıkan ajanın yetkisinin nasıl daraltılacağı |

### 1.4 Hafıza Framework'leri

> Ayrıntılı analiz → [ekosistem-analizi.md §4](../arastirma/2026-08-05-ekosistem-analizi.md)

| Framework | JARVIS İçin Değer | Al/Alma |
|---|---|---|
| **Letta (MemGPT)** | Blok hafıza: `memory_replace(old,new)` doğrulamalı düzenleme, `chars_current/limit` metadata, git-backed MemFS | **Desenleri kopyala, bağımlılık alma** (Mart 2026 API kırılması) |
| **Mem0** | Yazma kapısı (ADD/UPDATE/DELETE/NONE) — en kritik eksikliğimiz | **Prompt'u Apache-2.0'dan ödünç al** |
| **Vertex AI Memory Bank** | `enable_consolidation`, immutable revisions, ADK-native | **Değerlendir** — Firestore uyumu? |
| **Graphiti** | Bi-temporal geçersizleştirme | **Fikri al** (3 alan yeter), **Neo4j bağımlılığını alma** |
| **Cognee** | `memify` — kullanıma göre edge ağırlığı güncelleme | İlham |
| **EverOS** | Case → Skill terfisi (3 tekrar sonrası) | İlham — tarif katmanı için |

### 1.5 Politika, HITL ve Güvenlik Katmanı

> Ayrıntılı analiz → [ekosistem-analizi.md §5](../arastirma/2026-08-05-ekosistem-analizi.md)

| Çözüm | Durum | Alınacak Şey |
|---|---|---|
| **HumanLayer** | ❌ **Terk edilmiş** | Tip tasarımı: zorunlu gerekçeli ret, `ResponseOption` |
| **OPA / Rego** | ✅ Olgun | `allow/deny/requires-approval/not-applicable` 4-değerli karar modeli |
| **Permit MCP Gateway** | ⚙️ Yeni | Politikanın tool yüzeyinin şekliyle sağlanması ilkesi |
| **AgentLock / OAP** | ⚙️ v1.6 | Ed25519 imzalı, hash-zincirli receipt |
| **ADK `require_confirmation`** | ⚗️ @experimental | Session service kısıtı doğrulanmalı |

### 1.6 Ses Hattı, Anti-Spoofing ve Konuşmacı Kimliği

> Ayrıntılı analiz → [ekosistem-analizi.md §6](../arastirma/2026-08-05-ekosistem-analizi.md)

🔴 **En Kritik Bulgu:** ECAPA-TDNN'in sentetik seslere karşı SPF-EER'i **%30.75** (SASV 2022). 5 saniyelik ses örneğiyle MIT lisanslı, Türkçe destekli Chatterbox ile zero-shot klon üretiliyor. NIST SP 800-63B ses biyometrisini **açıkça yasaklıyor**. Google Voice Match ile cihaz kilidi açmayı kaldırdı. **Anti-spoofing olmadan ses kimliği yetki kapısı olarak kullanmak, sistemi tanımlı olarak açık bırakıyor.**

> **Durum (Rev 3.1):** Bu bulgunun acil yarısı kapandı. CM modeli (`nii-yamagishilab/mms-300m-anti-deepfake`) 6 Ağu'da prod'a girdi (`brain/app/antispoof.py`, eşik 0.85; spike'ta gerçek konuşma P_fake < 0.001, gTTS sentetik > 0.998) ve galeriye yazan üç kapı (enroll fail-closed, "bu bendim" onayı, challenge) CM'e bağlandı (11 Ağu). **Kalan yarım:** Chatterbox-sınıfı güçlü klon karşısında davranış ölçülmedi (spike zayıf gTTS spoof'la yapıldı) ve eşik kalibrasyonu (AS-Norm/cohort/QMF/DET) hâlâ yok — bkz. §7.

### 1.7 Yerel LLM ve Çıkarım Altyapısı

| Araç | Rolü | JARVIS İle İlişki |
|---|---|---|
| **Ollama** | Yerel model yönetimi | JARVIS bulut-öncelikli; yerel LLM opsiyonel |
| **llama.cpp** | Saf çıkarım performansı | Aynı |
| **LM Studio** | GUI + kuantizasyon kontrolü | Aynı |
| **vLLM / TGI** | Sunucu taraflı yüksek verim | Cloud Run'da GPU instance ile kullanılabilir |

**JARVIS için çıkarım:** Bulut-öncelikli mimarimiz (Ilke 9) yerel LLM çalıştırmayı birincil yol yapmaz. Ama **gizlilik gerektiren bazı görevler** (profil analizi, ses işleme) için cihaz-üstü küçük modeller (EmbeddingGemma, Personal VAD) stratejik.

---

## 2. JARVIS'in Gerçek Konumu — Sentez

### 2.1 Üç Cümlede Konum

1. **Mimari tezimiz doğru ve yalnız.** Cloud Run + scale-to-zero + ince istemci kombinasyonunu **kimse yapmıyor**. Hermes, OpenClaw, QwenPaw, ZeroClaw, DeepSeek Harness — hepsi always-on daemon veya yerel-öncelikli.

2. **Ayırt edici özelliklerimizden ikisi eridi, ikisi sağlam.** *Kendi mobil istemcisi* artık ayırt edici değil (OpenClaw Android+WearOS'ta önümüzde). *Onay katmanı* standart oldu. Ama **scale-to-zero bulut beyin** ve **sesli biyometrik yetki kapısı** hâlâ tamamen bize ait — ve ikisinin **birleşimi** hiç kimsede yok.

3. **En güçlü saydığımız özellikte en büyük açık büyük ölçüde kapandı; kalanı ölçüm.** Ses kimliği yetki kapısı artık anti-spoofing CM kapısının arkasında (6–11 Ağu, canlı). Güçlü klon karşısındaki davranış ve eşik kalibrasyonu ise hâlâ ölçülmemiş.

### 2.2 JARVIS'in 4 Ayırt Edici Özelliği — Kim Yapıyor?

| Özellik | Durum | Rakipler |
|---|---|---|
| **Bulut-yerli / scale-to-zero** | ✅ **Kimse tam yapmıyor** | Hermes en yakın (terminal hibernate) ama gateway always-on |
| **Sesli biyometrik yetki kapısı** | ✅ **Fiilen kimse yapmıyor** | Omi Speech Profile var ama yetki kapısı değil |
| **Kendi mobil istemcisi** | ❌ **Artık bize özgü değil** | OpenClaw Play Store'da; Omi Flutter |
| **Eylem onay katmanı** | ⚠️ **Artık standart** | QwenPaw en kapsamlısı; herkesin bir biçimi var |

### 2.3 Farkımız Nerede?

**Tek kelimeyle: BİRLEŞİM.**

- Bulut-yerli + scale-to-zero → **maliyet**
- Ses kimliği + anti-spoofing (gelecek) → **"kim istedi"**
- Politika katmanı + onay kuyruğu → **"ne yapılabilir"**
- Ajan fabrikası + yetki daraltma → **"ne kadar güç verildi"**

Bu dört eksenin **aynı sistemde birleşmesi** hiç kimsede yok. Her birini tek başına yapan var; hepsini bir arada yapan yok.

---

## 3. Temel İlke: "Piyasada Varsa Al, Sadece Boşlukları Kapa"

### 3.1 Neden "Sıfırdan Üretim" Bitti?

Tek geliştirici olarak:
- Jetpack Compose UI yazmak → **zeka geliştirmek değil**
- WebSocket ses geçitleri kurmak → **zeka geliştirmek değil**
- WhatsApp/Telegram bot yazmak → **zeka geliştirmek değil**
- DOM parser kodlamak → **zeka geliştirmek değil**

Bu boru hatları enerjinin **%80'ini** tüketiyor. Asıl hedefe (otonomi, öğrenme, ikame zekası) zaman bırakmıyor.

### 3.2 JARVIS'in Gerçek Kimliği (Dokunulamaz Çekirdek)

JARVIS bir "Android uygulaması" ya da "chat istemcisi" **değildir**. JARVIS:

1. **Güvenlik ve İzin Otoritesidir** — Yetki matrisi, HITL, biyometrik ses kimliği, taint izleme
2. **Kişisel Sürekli Öğrenme Motorudur** — Ders defteri, tarifler, kullanıcı profili, ACE curator
3. **Kadir'in Dijital İkamesidir** — Asistan kimliği, görev döngüsü, bütçe farkındalığı

**Geri kalan her şey — UI, ses borusu, mesajlaşma köprüleri, tarayıcı otomasyon — "boru hattı"dır ve piyasadaki en olgun çözüme devredilir.**

---

## 4. Katman Katman İkame Haritası

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    DIŞ DÜNYA — GİRİŞ / ÇIKIŞ ADAPTÖRLERI                     │
│                                                                             │
│  [Mobil/Sensör]    [Saat]         [Mesajlaşma]       [Ses/RTC]   [Tarayıcı] │
│  HA Companion      Wyoming Sat.   Matrix/Beeper      LiveKit     Browser-   │
│  (veya MCP node)   HA Wear OS     OpenClaw GW        Pipecat     Use        │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │  Standart: Webhook / MCP / SSE / WebSocket
                              │
┌─────────────────────────────▼───────────────────────────────────────────────┐
│                     JARVIS BEYNİ (CLOUD RUN — SCALE-TO-ZERO)                │
│                                                                             │
│  ┌───────────────────────────┐  ┌─────────────────────────────────────────┐ │
│  │ BİYOMETRİK KİMLİK        │  │ MERKEZİ POLİTİKA & HITL                │ │
│  │  ECAPA/ReDimNet Speaker-ID│  │  4-değerli karar motoru                │ │
│  │  Anti-Spoofing CM kapısı  │  │  Oturum taint bayrağı                  │ │
│  │  Challenge-response (T3)  │  │  FCM onay kuyruğu + tipli ret         │ │
│  │  PerTh filigran tespiti   │  │  Hash-zincirli audit kaydı            │ │
│  └────────────┬──────────────┘  └──────────────┬──────────────────────────┘ │
│               │                                │                           │
│  ┌────────────▼────────────────────────────────▼──────────────────────────┐ │
│  │ ORKESTRATÖR & UZMAN AJANLAR (Google ADK)                               │ │
│  │  Sekreter · Operatör · Geliştirici · Arşivci · Fabrika (K1→K3)        │ │
│  │  MCP hub · A2A gelecek entegrasyon                                    │ │
│  └────────────┬──────────────────────────────────┬───────────────────────┘ │
│               │                                  │                         │
│  ┌────────────▼──────────────┐  ┌────────────────▼───────────────────────┐ │
│  │ HAFIZA & BİLİŞSEL EVRİM  │  │ YÜRÜTME & ARAÇ KAZANIM MOTORU        │ │
│  │  Yazma kapısı (Mem0 fikri)│  │  MCP tool sunucuları                  │ │
│  │  Bi-temporal geçersizleşt.│  │  Browser-Use worker                   │ │
│  │  ACE Curator (delta güncl)│  │  Kademeli ajan fabrikası              │ │
│  │  Tarif katmanı (Case→Skill│  │  Auxiliary model slotları             │ │
│  │  Hafıza regresyon seti    │  │  Satellite deseni (cihaz tool'ları)   │ │
│  └───────────────────────────┘  └────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 4.1 Adaptör Katmanı — "Al, Bağla"

| Alan | İkame Çözüm | Kapatılacak Boşluk (JARVIS yazacak) |
|---|---|---|
| **Mobil / Sensörler** | Home Assistant Companion veya özel MCP node | `events.py`: cihaz bağlamını okuma; `fcm.py`: onay pushları |
| **Saat** | Wyoming Satellite + HA WearOS | PCM akışını speaker-ID hattına yönlendirme |
| **Mesajlaşma** | Matrix/Beeper köprüleri veya OpenClaw Gateway | `policy.py`: giden mesaj filtresi; taint zarfı |
| **Ses/RTC** | LiveKit / Pipecat (sunucu tarafı STT/TTS) | ECAPA + CM kaskad kapı; Personal VAD; Smart Turn |
| **Tarayıcı** | Browser-Use (Pi/masaüstü worker) | Görev bütçesi + HITL onay kapısı |
| **Kodlama Otomasyonu** | OpenHands / SWE-Agent (alt-ajan olarak) | ADK orkestratöründen delegasyon + sonuç doğrulama |
| **Akış Otomasyonu** | n8n (opsiyonel, HA ile birlikte) | JARVIS politika katmanından geçen trigger'lar |

### 4.2 Çekirdek Katman — "Yaz, Bu Senin Hendek"

| Alan | JARVIS'e Özgü Kod | Rakiplerdeki En İyi Referans |
|---|---|---|
| **Speaker-ID + CM + yetki katmanlaması** | `speaker.py`, `antispoof.py`, T0–T3 katmanları | Omi (transkript etiketleme, yetki kapısı değil) |
| **Politika motoru** | `policy.py`, 4-değerli karar, taint bayrağı | QwenPaw (en kapsamlı onay), OPA (deklaratif) |
| **Onay kuyruğu + FCM + Android kartı** | `approvals.py` | HumanLayer (terk edilmiş ama tip tasarımı değerli) |
| **Hafıza konsolidasyonu** | `memory.py` + yazma kapısı | Mem0 prompt, Letta blok şeması |
| **Ders defteri + ACE curator** | `workspace.py`, delta güncelleme | Hermes background review, ACE arXiv makalesi |
| **Tarif katmanı** | *(yok, planlı)* Case→Skill şeması yazılacak — Rev 3.0 bunu mevcut kod gibi listeliyordu | EverOS, Letta Skills, AWM |
| **Ajan fabrikası + yetki daraltma** | `factory.py`, TTL + bütçe + kayıt defteri | Hermes provenance, DAAP taslağı |
| **Audit log** | OCSF 6003 alan adları + hash zinciri | AgentLock Ed25519, ZeroClaw tool receipts |
| **Asistan kimliği zırhı** | Persona kuralları + prompt koruma | Leon Satellite (cihaz-spesifik tool'lar) |

---

## 5. Hermes Agent'ın "Self-Improving Skills" Mekanizması — En Değerli Rakip Mekanizma

> Bu mekanizma JARVIS'in ajan fabrikası + araç kazanım merdiveni hedefinin **çalışan karşılığı** ve tahmin edilenden çok daha olgun.

**Kısaca nasıl çalışıyor:**
1. Her turdan sonra **daemon thread'de ikinci bir ajan fork'u** konuşmayı replay eder
2. **Negatif liste:** "araç bozuk" tarzı olumsuz iddiaların kalıcı ders olarak yazılması **yasak** — model bir gözlemi kalıcı bir kısıta dönüştürürse aylarca kendi kendini reddeder
3. **Write-origin provenance:** ajan-üretimi vs kullanıcı-üretimi ayrı izlenir
4. **Küratör:** `active → stale (30g) → archived (90g)`, **asla silmez**

**JARVIS'e taşınacak:**
- Tur-sonrası "ders defteri" fork'u (H1)
- Write-origin provenance + yaşam döngüsü küratörü (H2)
- İki hızlı onay yüzeyi: inline gist vs out-of-band diff (H3)
- Görev başına auxiliary model slotu + digest replay (H4)

---

## 6. DeepSeek Harness — Yeni Oyuncu Değerlendirmesi

**Durum:** 13 Ağustos 2026'da developer preview olarak duyuruldu. MIT lisanslı. **1 haftalık.**

**İlginç olan:**
- "Everything is a plugin" felsefesi (Cordis meta-framework)
- YAML tabanlı konfigürasyon — model/tool/sandbox/scheduler hepsi takılıp çıkarılabiliyor
- Çoklu-ajan orkestrasyon desteği

**JARVIS için değerlendirme:**
- **Benimseme: HAYIR.** Developer preview, kırılma riski yüksek, ADK'dan geçiş maliyeti orantısız.
- **İlham: EVET.** Plugin mimarisi JARVIS'in MCP hub'ına benzer ama daha tutarlı. YAML tabanlı ajan tanımı Fabrika K2/K3 için referans olabilir.
- **İzle:** Eğer 6 ayda olgunlaşırsa ve ADK yetersiz kalırsa yeniden değerlendir.

---

## 7. Önceliklendirilmiş Boşluk-Kapama Yol Haritası — TEK HARİTA (Rev 3.1)

> **Rev 3.1 değişikliği:** 3.0'daki fazlar iki ayrı gerçekle çakışıyordu: North Star §12 katman haritası ve 11 Ağu ürün yüzeyi haritası ([2026-08-11-urun-yuzeyi-yol-haritasi.md](2026-08-11-urun-yuzeyi-yol-haritasi.md)). Tipli ret hem burada (Faz 2 #10) hem Onay Kartı 2.0 planında sayılıyordu; anti-spoofing "acil" yazılıyordu ama canlıydı. Efor tahminleri 5 Ağu analizinden kalır ([ekosistem-analizi.md §7.2](2026-08-05-ekosistem-analizi.md)); durumlar **22 Ağustos repo durumuyla** işaretlendi.
>
> Durum anahtarı: ✅ canlı/kanıtlandı · 🟡 kısmen · ❌ yapılmadı · 🔒 Kadir kararı bekliyor

### Kapandı — hedef listesinden çıkar

| İş | Ne zaman / kanıt |
|---|---|
| Anti-spoofing CM kapısı + T0–T3 yetki katmanlaması (eski Faz 0 #1) | ✅ CM modeli canlı (`brain/app/antispoof.py`, eşik 0.85; 6 Ağu deploy). Galeriye yazan üç kapı CM'e bağlandı: enroll fail-closed, "bu bendim" kanıt-okuma, challenge taze-CM (11 Ağu). Prod imaj `cm-60e568b` |
| Tipli ret + hazır gerekçeler (eski Faz 2 #10 = ürün yüzeyi F3) | ✅ Sunucu tarafı: `decide()` zorunlu-gerekçe guard'ı, `/reject` gövdesi, `REJECT_REASONS` (`approvals.py:104`); ret gerekçesi modele canlı ADK Event + restart'ta transcript replay ile ulaşıyor. Fix re-review CLEAN (22 Ağu, commit `96252ed`). İstemci yarımı Onay Kartı 2.0 Task 5-6 |
| Araç-başına geri alınabilirlik tablosu, bilinmeyende fail-closed | ✅ `config.TOOL_REVERSIBILITY` + `is_reversible()` (`config.py:179,239`) — Onay Kartı 2.0 Task 1 |
| Karar bağlamının onay kaydına taşınması (actor/trust/cause/operand/reversible) | ✅ Sunucu yazıyor (Task 2); istemci henüz okumuyor — F2'nin kalan yarımı aşağıda |

### Devam ediyor — in-flight işler

| İş | Durum |
|---|---|
| **Onay Kartı 2.0** (F2 kart bağlamı + hash-zincirli kayıt + istemci kartı) | Task 1–3 ✅ · **Task 4 sıradaki**: `decision_log` hash zinciri + geri-alınabilirliğe göre zaman aşımı *(eski Faz 2 #13 bununla tekilleştirildi)* · Task 5–6 istemci DTO + kartın kendisi |
| **Y4 eksikleri** (North Star §12) | Keep MCP ❌ (master token 🔒 Kadir'in) · Wear OS asgari 🟡 — W0 cihaz-token altyapısı canlı, W1 çekirdek T1-T4 hazır ama E2E'siz; saat credential kararı 🔒 |
| **Ürün yüzeyi dilimleri** (11 Ağu sırasıyla) | F7 sesli transkripti sohbete yazma (S) → F13+F9 rapor görünürlük bug'ı + adım ilerlemesi (S-M) → F1 akış/SSE (M). Ölçülen üç boşluk: akış yok (`main.py` olay akışını atıyor), sesli arama zaman çizgisinin dışında (`voice-{user_id}` sabit oturum + tam ekran overlay), retro/görev raporları `conversations.touch()` çağrılmadığı için uygulamada görünmüyor |

### Sıradaki — hendek işleri (öncelik sırasıyla)

| # | İş | Efor | Not |
|---|---|---|---|
| 1 | Oturum taint bayrağı (P4) | 1–2g | YEŞİL-tool-kötü-argüman kaçağı; `policy.py`'de bugün hiç taint izi yok (grep 0) |
| 2 | Mem0 yazma kapısı (ADD/UPDATE/DELETE/NONE) | 1–2g | Çelişen olgular birkaç ayda kesin bozulma; prompt Apache-2.0'dan ödünç |
| 3 | Hafıza regresyon seti (40-60 soru, Türkçe) | 1g | Arşivci'nin bilgi yok etmesini yakalayan tek şey |
| 4 | Galeri sertleştirmenin kalan yarısı (drift bütçesi + versiyonlama) | 2–3g | Poisoning marj bandı var (`SpeakerService` docstring'de belgeli); drift/versiyonlama yok |
| 5 | Kalibrasyon hattı (cohort + AS-Norm + QMF/DET) + güçlü klon testi | 4–6g | CM eşikleri tek gTTS spike'ıyla kondu; Chatterbox-sınıfı klon karşısında davranış ölçülmedi |
| 6 | Tam düpleks ses ölçümü: `setMode(MODE_IN_COMMUNICATION)` + AEC self-test + Personal VAD + Smart Turn | 5–8g | AEC yolu kurulu ama zinciri uyandıran `setMode` hiçbir yerde çağrılmıyor; `EXTRA_AUDIO_SOURCE`'ın cihazda gerçekten okunduğu da ölçülmedi — AEC bugün cihaza bağlı şans meselesi |
| 7 | ACE Curator + write-origin provenance + küratör | 4–6g | "Aynı hatayı iki kez yapma" ilk kez gerçek olur; K3'ün ön koşulu (§5) |
| 8 | Tur-sonrası ders defteri fork'u + negatif liste (Hermes H1-H4) | 2–3g | §5'teki en değerli rakip mekanizması |
| 9 | Tarif katmanı (Case→Skill) | 2–3g | ⚠️ `recipes.py` YOK — Rev 3.0 bunu mevcut kod gibi listeliyordu; sıfırdan şema işidir |
| 10 | İlk giriş adaptörü bağlantısı (Matrix/HA/MCP node) | 3–5g | "Al, bağla" ilkesi; mevcut Gmail push / Pub/Sub / FCM hatlarının ÜSTÜNE eklenir, yanına değil |
| 11 | Browser-Use worker + Satellite deseni | 6–10g | Donanımsız kısımları Faz D'ye değmeden yapılabilir |

### PerTh filigranı — bilinçli düşük-orta (kayıtlı karar, Rev 3.1 düzeltmesi)

Rev 3.0 PerTh'i "⭐ En ucuz/en yüksek getiri, Faz 0 #2" diye listeliyordu. **6 Ağustos Kadir kararı:** asistan sesi Kadir'in sesinden klonlanmayacak (istediği kişinin örnek sesiyle seçilecek) — bu, kendi-kendini-spoof'etme riskini büyük ölçüde düşürdü ve PerTh önceliği düşük-ortaya indi. Risk tamamen gitmez: seçilen ses Kadir'e benzerse ya da ileride kendi sesi konursa risk geri gelir; o yüzden listeden çıkarılmadı, sadece sırası düştü.

---

## 8. "Yapma" Listesi (Kaynak İsrafı Riski)

| Yapma | Neden |
|---|---|
| **Mevcut Android istemcisini HA/PWA ile değiştirme** | İstemci ürün yüzeyidir (11 Ağu Kadir düzeltmesi: UI ana iş yüzeyidir); S23/Pixel'deki uygulama, onay kartları ve ses akışı kalır. İlke yalnızca *yeni* kabuklar içindir: yeni bir yüzey gerektiğinde hazır çözüm (PWA) değerlendirilir, Compose sıfırdan yazılmaz |
| **WhatsApp/Telegram bot'u sıfırdan yazma** | Matrix köprüleri veya OpenClaw Gateway kullan |
| **Ses geçidini sıfırdan yazma** | LiveKit / Pipecat / Wyoming kullan |
| **DOM parser / tarayıcı otomasyon yazma** | Browser-Use kullan |
| **Kendi hafıza framework'ünü yazma** | Firestore + find_nearest doğru; desenleri Letta/Mem0'dan al |
| **LangGraph'ı bağımlılık yapma** | ADK zaten orkestratör; ikinci çerçeve yükü orantısız |
| **DeepSeek Harness'ı benimseme** | 1 haftalık developer preview |
| **Tam OPA'ya geçme** | Tek kullanıcıda Rego orantısız; YAML + test %80'i verir |
| **HumanLayer'ı benimseme** | Terk edilmiş |
| **Türkçe ECAPA fine-tune yapma** | Kazancı belirsiz, veri kıt (1.829 konuşmacı) |
| **Embedding'i sıfırdan eğitme** | gemini-embedding-001 (768 dim) veya BGE-M3 kullan |

---

## 9. Solo Geliştirici Kazanım Tablosu

```
ESKİ YOL (Her Şeyi Sıfırdan)              YENİ YOL (İkame + Boşluk-Kapama)
─────────────────────────────              ──────────────────────────────────
[ Compose Chat UI            ] ──► DEVRET  [ HA / PWA / Hazır UI        ] (0 gün)
[ Wear OS Yeni Yüzeyi        ] ──► ÖNCE    [ W0/W1 yatırımını bitir    ] (kod hazır)
[ WebSocket Ses Geçidi        ] ──► DEVRET  [ LiveKit / Pipecat          ] (0 gün)
[ WhatsApp/Telegram Webhooklar] ──► DEVRET  [ Matrix Köprüleri           ] (0 gün)
[ DOM Parser / Tarayıcı       ] ──► DEVRET  [ Browser-Use                ] (0 gün)
─────────────────────────────              ──────────────────────────────────
BAKIM YÜKÜ: ~%100 (tıkanma)               BAKIM YÜKÜ: ~%15 (yönetilebilir)

Kalan %85 → Güvenlik + Öğrenme + İkame Zekası
```

---

## 10. Sonuç

Bu strateji belgesi şunu söylüyor:

1. **Piyasa olgun.** Kişisel asistan kategorisinde 10+ ciddi açık kaynak proje var. Ajan çerçevelerinde ADK, LangGraph, CrewAI, DeepSeek Harness gibi güçlü altyapılar var. MCP ve A2A gibi endüstri standartları yerleşiyor.

2. **JARVIS'in gerçek hendek'i tekil bir özellik değil, birleşim.** Bulut-yerli scale-to-zero + sesli biyometrik yetki kapısı + politika motoru + ajan fabrikası birleşimi **hiç kimsede yok**.

3. **Güvenlik temeli kuruldu; kalan yarım ölçüm.** Anti-spoofing CM kapısı canlıda (6–11 Ağu). En acılık artık eşik kalibrasyonu, güçlü-klon ölçümü, taint bayrağı ve hafıza yazma hijyenindedir.

4. **"Sıfırdan üretim" dönemi bitti.** Giriş/çıkış adaptörleri piyasanın en olgunlarına devredilecek. JARVIS'in enerjisi yalnızca hendek'e — güvenlik, öğrenme, ikame zekası — akacak.

---

*Bu belge, [2026-08-05 Ekosistem Analizi](2026-08-05-ekosistem-analizi.md) üzerinde inşa edilmiştir. Kaynak-kod-düzeyinde doğrulama notları, benchmark tabloları ve araştırma kaynakları o belgede yer almaktadır.*

*Rev 3.1 (22 Ağu 2026): durum iddiaları repoya karşı doğrulandı (CM canlı, tipli ret sunucu tarafı kapandı, `recipes.py` yok); PerTh ve UI satırları kayıtlı kararlarla uyumlu hale getirildi; yol haritası North Star §12 + ürün yüzeyi haritasıyla tek haritada birleştirildi.*
