# Katman 2b — Dilim 3a: Kadir Ses-Kimliği (adaptive voiceprint + risk-tabanlı güven füzyonu) — Tasarım / Spec

**Tarih:** 2026-07-24
**Durum:** Onaylandı (brainstorming) → writing-plans'e hazır
**Kapsam:** Katman 2b Dilim 3'ün **ilk alt-dilimi (3a)**: sunucu tarafı ses-kimliği çekirdeği. Native ses (3b) ve kişiselleştirilebilir çıktı sesi (3c) ayrı dilimlerde, ayrı spec'lerde.

İlke: **idareten/geçici çözüm yok** — her parça production-grade, araştırmayla kanıtlanmış, teknik borç bırakmayan, North Star'a ("ikame") hizmet eden. (Bkz. memory `nihai-amaca-uygunluk`, `kadir-ses-kimlik`.)

---

## 1. Amaç

Jarvis, ses akışında **Kadir'i sesinden tanısın** — ve bunu statik bir voiceprint'le değil, **kendini sürekli besleyen (adaptive)**, **cihaz/kanal değişimine dayanıklı**, **gündelik hayatta kısıtlamayan** ama **bağlam riskli olduğunda sıkılaşan** genel bir kimlik/güven çekirdeğiyle yapsın.

Bu, sadece kişiselleştirme değil; North Star'daki "ikame" için **kimlik/güvenlik katmanı** — Jarvis'in kiminle konuştuğunu bilmesi ve buna göre eylem yetkisini modüle etmesi.

**Başarı ölçütü (bu dilim, saf sunucu çekirdeği):**
1. Kadir'in kayıtlı sesiyle gelen bir utterance **doğrulanır** (is-Kadir, skor); farklı bir konuşmacı **doğrulanmaz**.
2. Yüksek-güvenli utterance'lar profili **besler** (self-feeding); düşük-skorlu (sahtekâr) örnek profili **kaydırmaz** (poisoning'e dayanıklı).
3. Kimlik + bağlam, mevcut yeşil/sarı/kırmızı politika kararını **modüle eder**: `foreground` bağlamda gündelik kullanım **hiç kısıtlanmaz**; `locked`/`ambient` bağlamda doğrulanmamış konuşmacı hassas eylemlerde **sıkılaşır**.
4. Hepsi **scriptli WS test harness'ı + gerçek ses fixture'ları** ile kanıtlanır (canlı istemci gerekmez).

## 2. Kapsam

**Dahil:**
- `jarvis-voice` içinde in-process **ses-kimliği modülü** (`app/speaker.py`): ECAPA-TDNN embedding + doğrulama + adaptive galeri.
- **Adaptive, kanal-farkındalı voiceprint** (anchor + adaptive set, iki eşik, self-feeding, poisoning guard).
- **Risk-tabanlı güven füzyonu** (`TrustContext` → `TrustAssessor` → `TrustLevel`) ve mevcut **politika katmanı modülasyonu**.
- **Bootstrap enrollment endpoint** (`POST /api/voice/enroll`).
- **WS protokol genişletmesi** (`voice_protocol.py` hello: `device_hint`, `presence`) — geriye dönük uyumlu.
- **Scriptli WS test harness'ı + ses fixture'ları** ile uçtan uca doğrulama.

**Dışında (bilinçli — sonraki dilimler/katmanlar):**
- Native ses istemcisi (mic + enrollment UI + rozet) — **Dilim 3b**.
- Kişiselleştirilebilir asistan çıktı sesi — **Dilim 3c**.
- Masaüstü yüzeyi (ayrı desktop app veya Jarvis-as-MCP / masaüstü erişimi) — ayrı **yüzey dilimi**.
- **Tam who-said-what diarization** (çok-konuşmacı segmentasyonu). Bu dilimde yalnızca tek-baskın-konuşmacı doğrulaması; opsiyonel "başka konuşmacı olası" sinyali bir güven girdisi olarak eklenebilir ama tam diarization **ertelendi** (bkz. §8, §17).
- `locked`/`ambient` bağlamının **gerçek tetiklenmesi** (istemciden canlı kilit sinyali) — native/ASSIST dilimi. Bu dilimde mekanizma kurulur ve test harness'ıyla kanıtlanır.

### 2.1 Dilim 3 decomposition ve PWA'nın emekliye ayrılması

Spec `2026-07-24-katman-2b-native-chat-design` §2.1'deki "Dilim 3" aslında üç bağımsız yeteneği paketliyordu; ayrıştırıldı:

| Alt-dilim | İçerik | Bağımlılık |
|---|---|---|
| **3a (bu spec)** | Sunucu tarafı ses-kimliği çekirdeği | İstemci-agnostik; **native ses'e bağımlı değil** |
| 3b | Native ses (2a WS'ini Android'e taşıma) + in-app enrollment/rozet | 3a çekirdeğini devralır |
| 3c | Kişiselleştirilebilir çıktı sesi (Gemini prebuilt + custom TTS) | bağımsız |

**PWA emekliye ayrılıyor:** `web/` kalıcı yüzey değil (zaten native-chat spec §3'te "2b devreye girince emekliye ayrılır" notu vardı). Kalıcı yüzeyler: **Android native** + gelecekte **masaüstü** (ayrı app veya API/MCP). Bu dilim PWA'yı **ne kullanır ne genişletir** — çekirdek **istemci-agnostik `/ws/voice` sözleşmesi** üzerinde yaşar, dolayısıyla PWA bağımsız olarak silinebilir/kalabilir; 3a etkilenmez.

## 3. Mimari genel bakış

**Anahtar gerçek:** Ham mic sesi zaten sunucuda. [voice.py:59-60](../../../brain/app/voice.py#L59-L60) — istemci **ham PCM16 mono 16kHz** ([voice_protocol.py:10](../../../brain/app/voice_protocol.py#L10)) yolluyor; `_pump_mic_once` bunu Gemini Live'a iletmeden **önce** `data` olarak görüyor. Bu, ses-kimliğinin doğal müdahale noktası. Sözleşme **istemci-agnostik**: PWA/Android/masaüstü fark etmez.

```
JARVIS/
  brain/
    app/
      speaker.py       (YENİ — ECAPA embedding + verify + adaptive galeri)
      trust.py         (YENİ — TrustContext + TrustAssessor + TrustLevel)
      voice.py         (genişletilir — utterance buffer, verify, trust, adapt)
      voice_protocol.py(genişletilir — hello: device_hint, presence; evt_speaker)
      policy.py        (genişletilir — trust ile modülasyon)
      main.py          (genişletilir — POST /api/voice/enroll; text yolu default HIGH)
      memory.py / speaker_store  (Firestore speaker_profiles)
```

**Nerede çalışır — in-process (jarvis-voice):**
- **Neden in-process:** tek kullanıcı, düşük trafik; WS servisi zaten `min-instances=1` ile sıcak tutulmalı (soğuk-başlangıçta sesli oturum kötü UX) → torch/SpeechBrain'in ağırlığı **bir kez** amortize olur, kare-başı network hop yok.
- **Model lazy singleton:** ilk utterance'ta yüklenir; sonraki utterance'lar sıcak.
- **Kaçış kapısı:** modül sınırı temiz; container ağırlığı/cold-start **ölçülen** bir sorun olursa `jarvis-speaker` sidecar'ına çıkarmak önceden planlı, trivial refactor (utterance başına 1 HTTP). Şimdi YAGNI.

## 4. Yaklaşım kararı (production-grade, gerekçeli)

| Yaklaşım | Karar | Neden |
|---|---|---|
| **A — Kendi barındırılan ECAPA-TDNN** | **SEÇİLDİ** | SpeechBrain `spkrec-ecapa-voxceleb`, Apache-2.0, 16kHz mono (bizim rate ile **birebir**, resampling yok), cosine-distance doğrulama, ~%0.80 EER. Tam kontrol, ek bulut yok, mevcut "embedding-in-Firestore" desenini kullanır. Bedeli: torch/speechbrain container ağırlığı (§3'te amortize). |
| B — Google Cloud STT diarization | Elendi | Anonim etiket verir ("spk_1"), "Kadir" DEMEZ → kimliği **doğrulayamaz**. |
| C — 3. taraf hosted (Azure Speaker Rec vb.) | Elendi | Kadir'in biyometrik voiceprint'ini GCP-dışı buluta gönderir (mahremiyet); erişim-kısıtı geçmişi var. Tek-kullanıcı/mahremiyet-hassas proje için uygunsuz. |

**ONNX notu:** ECAPA'nın ONNX export'u sancılı (FFT `torch.onnx.export`'ta desteklenmiyor, ağ 3 bloğa bölünüyor) ve torch'tan **yavaş** → doğrudan torch/SpeechBrain inference doğru seçim.

## 5. Adaptive voiceprint modeli (kanal-farkındalı) — çekirdek

`SpeakerProfile` = Kadir için bir embedding **galerisi** (tek vektör DEĞİL):

- **Anchor set (sabit):** bootstrap enrollment'taki ~5-8 temiz örnek. **Asla silinmez** → gerçek taban; profil sürüklenmesini (drift) engeller.
- **Adaptive set (kayan, sınırlı):** doğrulanmış **yüksek-güvenli** utterance'lar buraya eklenir. Sınır aşılınca **çeşitlilik-koruyan evict** (en gereksiz/yakın örneği düşür, çeşitliliği koru — salt recency değil, çünkü recency drift'e açık). Her örnek `device_hint` ile etiketli.
- **Kanal/cihaz dayanıklılığı:** Kadir farklı cihaz/kanaldan (telefon/kulaklık/tablet) konuştukça yüksek-güvenli örnekler galeriye girer → galeri kanalları **kendiliğinden kapsar**. Örnekler `device_hint` ile etiketlendiği için doğrulama gerekirse kanal-farkında olabilir.
- **Yeni cihaz cold-start'ı:** o kanaldan hiç örnek yoksa skor düşük olabilir; ama güven füzyonunda (§6) auth + `foreground` güçlü olduğundan **kısıtlama olmaz** ve o güvenli bağlamda profil yeni kanalı **hızla öğrenir** (aggressive adapt) — "yeni tablete geçtim" senaryosu sessizce absorbe edilir.

**Doğrulama:** gelen utterance embedding'inin galeriye (anchor ∪ adaptive) cosine benzerliğinin **top-k ortalaması** → varyasyona dayanıklı.

**İki eşik (poisoning'e karşı):**
- `SPEAKER_ACCEPT_THRESHOLD`: skor ≥ ise "Kadir" (politikaya/etikete). Altı → bilinmeyen konuşmacı.
- `SPEAKER_ADAPT_THRESHOLD` (> ACCEPT): skor ≥ ise profili **besle**. `ACCEPT ≤ skor < ADAPT` arası: kabul et ama **besleme**.
- Adaptasyon ek olarak **token=Kadir** iken yapılır (auth-korumalı). Anchor'lar sabit.
- Eşikler ampirik → `config.py`'de; enrollment sonrası Kadir'in birkaç örneğiyle kalibre edilir.

**Depolama (Firestore):** `speaker_profiles/{user_id}/samples/{auto_id}` alt-koleksiyonu, her doküman `{embedding: list[float], kind: "anchor"|"adaptive", device_hint: str, ts}`. Galeri sınırlı (~28 vektör × 192-dim) → ucuz; doğrulama in-process cosine (find_nearest gerekmez, tek kullanıcı). Mevcut `memory.py` embedding-in-Firestore deseniyle tutarlı.

## 6. Risk-tabanlı güven füzyonu (`trust.py`)

Kadir'in yönlendirmesi: verilen örnekler (kilit/cihaz/hastalık) **kural değil**; genel, **genişletilebilir** bir güven çerçevesi tasarlanır — örnekler onun sinyalleridir.

**`TrustContext`** — açık uçlu sinyal demeti (eksik sinyalde zarif düşer):
```
auth_verified : bool         # token → Kadir'in hesabı (mevcut auth)
device_hint   : str          # "phone" | "headset" | "tablet" | "unknown"  (istemciden)
presence      : str          # "foreground" | "locked" | "ambient" | ...   (istemciden, genişletilebilir)
voice_score   : float | None # voiceprint eşleşmesi; None = ses yok (text chat)
# gelecek dikişleri (bu dilimde KOD YOK, yalnızca açık arayüz): location, time_of_day, behavioral_pattern
```

**`TrustAssessor.assess(ctx) -> TrustLevel`** (`HIGH | MEDIUM | LOW`) — küçük, saf, test edilebilir füzyon fonksiyonu:
- `auth_verified` taban şart (yoksa bağlantı kurulmaz).
- `presence == foreground` (kilit açık) → **HIGH** (voice_score ne olursa olsun) — gündelik hayat leniency'si; voice yalnızca etiketler + besler.
- `presence in {locked, ambient}` → voice_score karar verir: eşleşme → HIGH/MEDIUM, eşleşmeme → **LOW**.
- `voice_score is None` (text) → auth + presence'a dayan (text = foreground/authed → HIGH).
- **Yeni sinyal eklemek = füzyona bir satır**; mimari değişmez.
- Eşikler/kademeler `config`'de — **ayarlanabilir**, kutsal tablo değil.

## 7. Kimlik → politika entegrasyonu

Politika = **2 eksenin fonksiyonu**: mevcut **zone** (yeşil/sarı/kırmızı = eylem hassasiyeti, [config.py:65-72](../../../brain/app/config.py#L65-L72)) × **TrustLevel** (kimlik güveni).

| | zone GREEN | zone YELLOW | zone RED |
|---|---|---|---|
| **HIGH** (bugünkü davranış) | allow | allow / dry_run | block |
| **MEDIUM** | allow | **onay iste** | block |
| **LOW** | allow (hassas olan flag) | **block/onay** | block |

- **"onay iste" mekanizması:** yeni bir akış değil — mevcut [policy.py:33-37](../../../brain/app/policy.py#L33-L37) `block` deseni: araç çağrısı yerine Jarvis'e "bu bölgede/güvende onaysız çalıştırılamaz, Kadir'e sor" mesajı döner; Jarvis de Kadir'den açık onay ister (INSTRUCTION'daki "politika engelini Kadir'den saklama" kuralı). Yani MEDIUM/LOW eskalasyonu bu kanıtlanmış deseni yeniden kullanır.
- **Gündelik hayat dokunulmaz:** `foreground` → HIGH → tablo bugünkü davranışla **birebir aynı**. Voiceprint kilit açıkken **hiçbir şeyi kısıtlamaz** — yalnızca etiketler + besler + latent ikinci faktör olarak hazır durur.
- **Mekanizma:** `policy_callback` `tool_context`'ten `TrustLevel` okur; **yoksa `HIGH` varsayar** → **text yolu (`/api/chat`) hiç değişmez**. Yalnızca voice bridge non-HIGH yazabilir (yalnızca riskli bağlamda).
- **RED daima block** (kimlikten bağımsız) — mevcut güvenlik tabanı korunur.
- Audit'e `trust_level`, `voice_score`, `presence`, `device_hint` de yazılır.

**Sinyal taşıma mekanizması (plan aşamasında ADK kaynağından doğrulanacak):** voice bridge utterance başına `TrustContext` hesaplar; o turdaki tool call'lara `TrustLevel`'i ulaştırır. Aday mekanizmalar: (a) ADK session state (`tool_context.state`), (b) session-anahtarlı bir trust holder'ı `policy_callback` closure'ının okuması. Kesin seçim ADK 1.36.2 `run_live` event sıralaması + state propagation semantiğine göre plan aşamasında netleşir (bu projenin `append_event`/`_is_other_agent_reply`'ı kaynaktan doğruladığı gibi). Default HIGH garantisi, mekanizma ne olursa olsun text yolunu korur.

> **KARAR (2026-07-24, kurulu google-adk 1.36.2 kaynağından doğrulandı): (b) seçildi.** (a) mekanik olarak **imkânsız**: `InMemorySessionService.get_session()` saklanan session'ı değil bir **kopyasını** döner (`_get_session_impl` → `_copy_session`, in_memory_session_service.py:186-202 / :54-58 / :48-51), `Runner.run_live(user_id=…, session_id=…)` da **kendi** kopyasını ayrıca çeker (runners.py:1049-1054, :401) ve invocation context'i onun üzerine kurar (:1055-1059); `ToolContext` = `Context` (tools/tool_context.py:27) ve `Context.__init__` `State(value=invocation_context.session.state, …)` bağlar (agents/context.py:69-72). Yani bridge'in `session.state` yazısı policy'ye **hiç ulaşmaz** (ampirik: bridge `{'trust_level': 'LOW'}` vs runner `{}` → default HIGH). `run_live(session=…)` bizim nesnemizi verirdi ama 1.36.2'de **deprecated** (runners.py:1016-1017, :1042-1048) → teknik borç, kullanılmadı. Uygulama: `app/voice_trust.py`, `(app_name, user_id, session_id)` ile anahtarlanmış process-içi holder; policy tarafı anahtarı **public** `tool_context.session` üzerinden okur (agents/readonly_context.py:59-62 + sessions/session.py:39-45). Provider **yalnızca** voice runner'ın agent'ına verilir (`main._init_voice`), böylece text yolu yapısal olarak etkilenmez.

## 8. Bileşen sınırları (temiz mimari)

| Modül | Sorumluluk | Bağımlılık |
|---|---|---|
| `speaker.py` — `embed(pcm16_16k) -> list[float]` | ECAPA embedding (lazy singleton model) | speechbrain, torch |
| `speaker.py` — `verify(vec, profile) -> (is_kadir, score)` | galeriye top-k cosine | saf Python |
| `speaker.py` — `SpeakerProfile` | anchor+adaptive galeri, `adapt(vec, device_hint)` evict/diversity | — |
| `speaker_store` (Firestore) | `load_profile / enroll_anchor / append_adaptive` | firestore |
| `trust.py` — `TrustContext`, `TrustAssessor` | sinyal füzyonu → `TrustLevel` | saf Python |
| `voice.py` (VoiceBridge) | utterance PCM buffer → turn sonu verify → trust → session sinyali + `evt_speaker` + koşullu adapt | speaker, trust, session |
| `policy.py` (`policy_callback`) | zone × TrustLevel modülasyonu; default HIGH | config |
| `main.py` — `POST /api/voice/enroll` | bootstrap: kayıtlı ses → anchor galeri | speaker, store, auth |
| `voice_protocol.py` | hello genişler (`device_hint`,`presence`); `evt_speaker` | — |

## 9. Veri akışı

**Ses yolu (utterance başına):**
1. Mic PCM kareleri Gemini Live'a giderken **aynı anda** utterance buffer'ına biriktirilir.
2. `input_transcription`/`turn_complete` sınırında: `embed(buffer)` → `verify(vec, profile)` → skor.
3. `TrustContext(auth, device_hint, presence, voice_score=skor)` → `TrustAssessor.assess` → `TrustLevel`.
4. `TrustLevel` o turun tool call'larına ulaştırılır (§7); `evt_speaker(role, verified, score)` istemciye gider (gelecek rozet için); transcript'e kimlik etiketi eklenir.
5. Skor ≥ `ADAPT` ve token=Kadir ise `profile.adapt(vec, device_hint)` → Firestore adaptive set güncellenir (self-feeding).

**Text yolu (`/api/chat`):** ses yok → `TrustLevel` yazılmaz → `policy_callback` default HIGH → **davranış değişmez**.

## 10. Enrollment (bootstrap)

- `POST /api/voice/enroll` (`Depends(require_user)` + allowlist): bir veya birden çok kısa ses klibi (WAV/PCM 16kHz) alır → her klip için embedding → **anchor** olarak `speaker_profiles/{user_id}/samples` altına yazar.
- Kaynak-agnostik: klipler telefon/bilgisayardan kayıtla üretilip POST edilir (bu dilimde in-app UI yok — 3b'ye ait). Plan aşamasında küçük bir enroll-yardımcı script'i (WAV → endpoint) sağlanır.
- Idempotency: enroll, mevcut anchor'ları değiştirir/ekler (net semantik plan aşamasında).

## 11. Protokol değişiklikleri (`voice_protocol.py`)

- Hello frame `{"token": ...}` → `{"token", "device_hint"?, "presence"?}`. **Geriye dönük uyumlu:** eksikse `device_hint="unknown"`, `presence="foreground"`.
- Yeni event: `evt_speaker(role, verified: bool, score: float) -> dict` — gelecek istemcilerde "🔒 Kadir doğrulandı" / "⚠ bilinmeyen konuşmacı" rozeti.
- Genişleme, 3b/native ve masaüstünün **rework'süz** devralması için şimdi yapılır.

## 12. Hata yönetimi / güvenlik / mahremiyet

- **False-reject (hasta/gürültü/yeni cihaz):** galeri + adaptasyon + kanal-farkındalık azaltır; `foreground` bağlamda zaten kısıtlama yok; ses reddedilse bile **yazılı giriş fallback** her zaman var → Kadir **asla kilitlenmez**.
- **Template poisoning:** yüksek `ADAPT` eşiği + sabit anchor'lar + token-korumalı adaptasyon. Test: düşük-skorlu örnek profili kaydırmıyor (§13).
- **Mahremiyet:** voiceprint biyometrik → yalnızca Kadir'in kendi Firestore projesinde, GCP-dışına gitmez (Approach A sebebi). **Ham enrollment sesi saklanmaz**, yalnızca embedding.
- **DATA-log (CLAUDE.md ilkesi):** utterance başına `voice_score`, eşikler, `TrustLevel`, `presence`, `device_hint`, adapt-edildi-mi, karar → tek çalıştırmada lokalize.
- **Zaman aşımı/hata:** model yükleme/embed hatası → utterance doğrulanamadı sayılır (`voice_score=None` gibi davranılmaz; `verified=False` + log), oturum düşmez; ses akışı bozulmaz.

## 13. Test stratejisi (production-grade)

| Katman | Kapsam | Araç |
|---|---|---|
| Birim (saf) | `verify` eşik sınırları; `SpeakerProfile.adapt` evict/diversity + **anchor korunması**; **poisoning senaryosu** (düşük-skor örnek profili kaydırmıyor); `TrustAssessor` tüm sinyal kombinasyonları (foreground→HIGH, locked+mismatch→LOW, voice=None→auth'a dayan); `policy_callback` zone×trust hücreleri + **default HIGH** | pytest, mevcut 93'e eklenir; fakes |
| Model entegrasyon | Gerçek kısa 16kHz örnekle embedding **determinizmi** (aynı ses→aynı vektör), farklı-konuşmacı **ayrımı** (Kadir vs impostor fixture) | pytest + ses fixture'ları |
| Uçtan uca | **Scriptli WS test harness'ı**: `/ws/voice`'a hello (token + device_hint + presence) → gerçek WAV akıtır → `evt_speaker` + doğrulama/adaptasyon/trust davranışını assert eder. `presence=locked` + impostor ile MEDIUM/LOW kademesini kanıtlar | pytest-asyncio + WS istemci + fixture |

Canlı istemci (PWA/native) gerektirmez; harness repeatable + CI'lanabilir → PWA UI'ından daha production-grade.

## 14. Kanıtlanmış teknik gerçekler (bu tasarımın dayanağı)

1. Ham mic sesi sunucuda **Gemini Live'a gitmeden önce** yakalanabilir. (kaynak: [voice.py:59-60](../../../brain/app/voice.py#L59-L60))
2. WS sözleşmesi **istemci-agnostik**, PCM16 mono 16kHz. (kaynak: [voice_protocol.py](../../../brain/app/voice_protocol.py))
3. ECAPA (`speechbrain/spkrec-ecapa-voxceleb`): Apache-2.0, **16kHz mono** (bizim rate ile birebir), cosine-distance verification, ~%0.80 EER. ONNX yolu sancılı/yavaş → torch. (kaynak: HF model kartı, SpeechBrain issue/discussion)
4. Gemini (Live dahil) **belirli kişiyi (Kadir) doğrulamaz**; diarization anonim etiket verir → ayrı voiceprint pipeline zorunlu. (kaynak: AI dev forum, memory `kadir-ses-kimlik`)
5. Politika `before_tool_callback` `tool_context` alır → session state erişimi var; kimlik sinyali buraya bağlanabilir. (kaynak: [agent.py:36](../../../brain/app/agent.py#L36), [policy.py:17](../../../brain/app/policy.py#L17))
6. Proje zaten Firestore'da embedding saklıyor (192-dim voiceprint aynı deseni kullanır). (kaynak: [memory.py](../../../brain/app/memory.py))

## 15. Plan aşamasında kesinleşecek (bilinçli açık uçlar)

- SpeechBrain/torch **kesin sürümleri** + Cloud Run **container ağırlığı ölçümü** (in-process kararının doğrulanması; gerekirse sidecar kaçış kapısı).
- ADK 1.36.2 `run_live`'da **utterance-verify → tool-call sıralaması** ve `TrustLevel` taşıma mekanizması (session state vs holder) — kaynaktan doğrulanacak.
- ECAPA embedding **boyutu** (muhtemelen 192) + `SPEAKER_TOPK`, `ACCEPT`/`ADAPT` eşikleri + adaptive `CAP` başlangıç değerleri (enrollment sonrası kalibrasyon).
- `speaker_profiles` şema kesinleştirme (alt-koleksiyon vs tek doküman + array) ve evict/diversity politikasının tam algoritması.
- Enroll idempotency semantiği + enroll-yardımcı script'i.
- Torch model dosyalarının Docker imajına gömülmesi vs runtime indirme (cold-start etkisi).

## 16. Kapsam dışı (explicit — borç değil, sınır)

- Native ses istemcisi / mic / in-app enrollment+rozet UI → **3b**.
- Kişiselleştirilebilir çıktı sesi → **3c**.
- Masaüstü yüzeyi (app / Jarvis-as-MCP / masaüstü erişimi) → ayrı yüzey dilimi.
- Tam who-said-what diarization → ertelendi (opsiyonel "başka konuşmacı olası" güven sinyali dışında).
- `locked`/`ambient` bağlamının canlı tetiklenmesi → native/ASSIST dilimi (mekanizma + harness testi bu dilimde).
