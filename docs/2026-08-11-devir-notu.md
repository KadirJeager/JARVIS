# Devir notu — 11 Ağustos 2026, 03:00 (Claude oturumu)

Bu belge taze bir oturumun **otonom** devam edebilmesi için yazıldı. Kadir uyuyor. Aşağıda "Kadir'e sor" yazmayan hiçbir şey için onay bekleme.

---

## 1. Tek cümlede

Ses kimliği hattının galeriye yazan **üç kapısı da CM'e bağlandı**, telefonun ses zinciri **ilk kez ölçüldü** (AEC gerçekten bağlıymış), ve bu işlerin planı `docs/superpowers/plans/2026-08-11-ses-kimligi-pixel-dogrulugu.md`'de 10 task olarak duruyor — **5'i bitti, 5'i sende**.

## 2. Canlı durum

| | Değer |
|---|---|
| Dal | `feat/antispoof-cm` — HEAD `39a9b0d` (main'in 28 commit önünde, **main'e merge YOK**) |
| Prod imaj | `gcr.io/your-gcp-project/jarvis-brain:cm-82193f0` |
| `jarvis-voice` | `00028-c44` · **4Gi** · CM warmup açık · threads 2 |
| `jarvis-brain` | `00035-kh9` · 3Gi · CM yüklenmiyor (bilerek) |
| Brain testleri | **877 geçti, 2 skip** (gece başında 863'tü) |
| Android | JVM süiti + `assembleDebug` yeşil; instrumented testler **artık derleniyor** (aşağıya bak) |
| Pixel 10 Pro | Tailscale `100.64.0.10` — adb portu **her açılışta değişir**, Kadir'den istemen gerekir |

⚠️ **Prod, main'de olmayan bir daldan koşuyor.** Bu gece kapatılmadı; merge kararı Kadir'in.

## 3. Bu gece kapanan işler

### Sunucu — üç CM kapısı (hepsi review'dan geçti)

| Commit | Ne |
|---|---|
| `410ff8b` | `POST /api/voice/enroll` artık her klibi CM'den geçiriyor. **Fail-closed**: kanıt yoksa 503. Anchor'lar değiştirilemez ve `anchor_score` hakemliğinin dayanağı olduğu için burada canlı yoldan farklı olarak kapalıya düşüyor. `CM_TIMEOUT_S` bütçesi var. |
| `3e76d6d` | "Bu bendim" onayı, söyleyiş anında **zaten kaydedilmiş** `cm_fake_prob`'u okuyor. Model yeniden koşmuyor. Kanıt yoksa `RuleViolation` → 400. |
| `abb8078` | Liveness challenge grant'i artık **taze** bir `cm_ok=True` istiyor. `False` ve `None` reddediyor. |

`abb8078`'in içinde bir güvenlik açığı da kapandı: `_verify_utterance` boş PCM tamponunda hiç yayın yapmadığı için, ham WebSocket'i süren biri önce gerçek sesle konuşup `cm_ok=True` bastırıp ardından **sessiz** bir `user_text` çerçevesiyle kodu geçebiliyordu. Artık söyleyiş-başına `_cm_verdict_fresh` bayrağı var.

> **Kırılganlık, kayda geçsin:** o bayrak bağlantı-başına tek boolean ve yalnız alım döngüsü **seri** olduğu, `_verify_utterance` challenge dalından önce **inline await** edildiği için güvenli. `_verify_utterance` bir gün `create_task` ile dağıtılırsa (ki `_run_turn` zaten öyle) ya da çerçeveler eşzamanlı işlenirse bu koruma sessizce çöker.

### İstemci — ses zinciri

| Commit | Ne |
|---|---|
| `44faa32` | **Ön koşul**: daldaki androidTest'ler 3 Ağustos'tan beri derlenmiyordu (3 fake'te `silentSignIn(force)` imzası eksik). Yani bu dalda hiç cihaz testi koşturulamamış. |
| `b11d08b` + `fa2dafd` | `AudioManager.setMode(MODE_IN_COMMUNICATION)` + manifest'e `MODIFY_AUDIO_SETTINGS`. Mod, `routeVoiceToSpeaker()`'dan **önce** kuruluyor; kapanışta **kaydedilen önceki** mod geri veriliyor. `fa2dafd` bir Critical'ı kapattı: yakalama korumasızdı, çağrı sırasında ekran döndürmek `onStartCommand`'ı yeniden tetikleyip "önceki mod"u `MODE_IN_COMMUNICATION`'ın kendisiyle eziyordu → cihaz **hiçbir zaman** normale dönmüyordu. |
| `c8b3592` + `39a9b0d` | AEC self-test telemetrisi. `39a9b0d` AEC tespitini isim-içinde-arama yerine **`descriptor.type` UUID**'sine çevirdi. |

## 4. Sahada ölçülenler — 11 Ağu 02:48, Pixel 10 Pro

```
I/AndroidMicSource: mic effects=Acoustic Echo Canceler,Noise Suppression aec=true silenced=false
CM: dur_in=2.42s dur_used=2.42s threads=2 load_ms=0 infer_ms=3328 p_fake=0.0006 verdict=bonafide
speaker.identify: score=0.5529 anchor_score=0.4300 verified=True cm_ok=True adapted=False anchors=7 adaptive=12
```

Dört sonuç:

1. **AEC gerçekten bağlı.** Aylardır varsayımdı. `setMode` işini yapıyor.
2. **Descriptor'ın adı "Acoustic Echo Canceler" — içinde "aec" geçmiyor.** Review'cı bu tam senaryoyu tahmin etmişti; cihaz birebir onu üretti. `39a9b0d` olmasaydı bu satır *bu donanımda* `aec=false` diyecekti. Düzeltme teorik değil, sahada kanıtlandı.
3. **`silenced=false`** → kendi TTS'imizin mikrofonu susturduğu hipotezi zayıfladı. 10 Ağustos'taki `anchor_score=0.0058` söyleyişi başka bir şey.
4. **`p_fake=0.0006`** — 10 Ağustos turunda 0.4231'e kadar çıkmıştı, şimdi benchmark seviyesinde. Yani o yüksek değerler **kanalın özelliği değil**, bozuk yakalamanın sonucu olabilir. Eşik kalibrasyonu tartışması bu veriyle yeniden okunmalı.

**Değişmeyen sorun:** `anchor_score=0.4300`, adapt kapısı `0.60`. Galeri hâlâ Pixel'i öğrenemiyor. Anchor'ların hepsi satılan S23'ün kanalından. **Tek çözüm enroll** — ve istemci yolu henüz yok (Task 8-9).

## 5. AÇIK ARIZA — ilk kelimeler kayboluyor

Kadir 02:48'de bildirdi: **söylediği cümlenin ilk kelimeleri yakalanmıyor.** Sunucuya tek söyleyiş ulaştı ve `dur_in=2.42s` idi.

**Teşhis edilemedi ve tahmin yürütülmedi.** Sebep: istemci hiçbir şey loglamıyor — `VoiceSession` durum geçişleri, `SpeechRecognizer` olayları, `relisten()` zamanlaması, hiçbiri. Elimizde mikrofonun ne zaman açıldığı, tanıyıcının ne zaman dinlemeye başladığı ve ilk partial'ın ne zaman geldiğine dair **hiçbir zaman çizelgesi yok**.

**Sıradaki oturumun ilk işi bu olmalı:** istemciye DATA seviyesinde zaman çizelgesi logu ekle (mic açıldı → recognizer başladı → ilk partial → final → frame gönderimi durdu/başladı, hepsi ms damgalı). Ölçmeden hipotez kurma. Bu, plana **yeni bir task** olarak eklenmeli.

İlgili ipucu: `EXTRA_AUDIO_SOURCE` inert olduğu için (Task 6) tanıyıcı **kendi mikrofonunu** açıyor — yani bizim `AudioRecord`'umuz ile tanıyıcının yakalaması **iki ayrı istemci, iki ayrı başlangıç anı**. İlk kelime kaybının en olası yeri burası, ama **kanıt yok**.

## 6. Sıradaki iş — SDD zaten kurulu

Plan: `docs/superpowers/plans/2026-08-11-ses-kimligi-pixel-dogrulugu.md`
Ledger: `.superpowers/sdd/2026-08-11-ses-kimligi-pixel-dogrulugu/progress.md` ← **önce bunu oku**, `Task <N>: complete` satırı olanları tekrar koşturma.

Kalan: **Task 6** (ölü `EXTRA_AUDIO_SOURCE` int'i + yanıltıcı yorumu sil), **7** (tr-TR tanıyıcı PFD probu — emülatörde), **8** (istemci challenge+enroll uçları), **9** ("Bu cihazı tanıt" akışı), **10** (cihazda uçtan uca — HITL).

`superpowers:subagent-driven-development` kullanılıyor: task başına taze subagent, her task sonrası **zorunlu** review, düzeltme turu, kapsamlı re-review. Bu gece 5 task'ta **1 Critical + 5 Important** yakalandı — hepsi review'da, hiçbiri kodlayanın kendi kontrolünde.

## 7. Kadir'e sorulacaklar (uyanınca)

1. **Saat credential kararı** — `feat/wear-w1-core` merge'ünü bloke ediyor. Kadir sordu: "LTE model mi, LTE yoksa Wi-Fi'da çalışır mı?" **Cevaplanmış teknik durum:** Wear OS'ta Data Layer köprüsü Bluetooth *ve* aynı Wi-Fi ağı üzerinden çalışır, yani "telefon başka odada" senaryosu köprü modelinde de sorunsuz. Dolayısıyla saatte anahtar saklamanın gerçek kazancı yalnız **telefondan uzakta VE ortak ağda değilken** var — bu da ancak **LTE'liyse** mümkün. LTE yoksa anahtar saklamak neredeyse sıfır fayda karşılığı kalıcı kimlik bırakır. **Saat modeli hâlâ bilinmiyor; önce onu öğren.**
2. **İlk-kelime arızası** için cihazda ikinci bir tur (enstrümantasyon eklendikten sonra).

## 8. Deploy — yetki verildi

Kadir 11 Ağu 02:32'de: *"hazır olduğunu düşündüğün şeyleri otomatik deploy edebilirsin"*. Yani sunucu tarafı review'dan geçmiş işler için ayrıca onay isteme.

**Bu gece deploy EDİLMEDİ** — dinleme turu canlı aramayı kesmesin diye ertelendi, sonra arıza çıktı. Task 1-3 (üç CM kapısı) **deploy edilmeyi bekliyor**.

### Deploy prosedürü (kanıtlanmış sıra)

```bash
cd /home/user/Projeler/JARVIS/brain
gcloud builds submit --tag gcr.io/your-gcp-project/jarvis-brain:cm-<kısa-sha> --project your-gcp-project .
# İKİ YAML'da da imaj etiketini ve nonce'u güncelle:
sed -i 's|jarvis-brain:cm-[a-f0-9]*|jarvis-brain:cm-<kısa-sha>|' deploy/jarvis-brain.yaml deploy/jarvis-voice.yaml
sed -i "s|client.knative.dev/nonce: '[^']*'|client.knative.dev/nonce: 'cm<kısa-sha>1'|" deploy/jarvis-brain.yaml deploy/jarvis-voice.yaml
gcloud run services replace deploy/jarvis-voice.yaml --region europe-west1 --project your-gcp-project
gcloud run services replace deploy/jarvis-brain.yaml --region europe-west1 --project your-gcp-project
```

**Deploy'u YAML değişikliğiyle birlikte commit et.** Bu gece iki kez drift yakalandı: canlıda 4Gi varken YAML'da 3Gi kalmıştı (bir sonraki `services replace` OOM'u geri getirecekti), ve deploy edilen imaj etiketi commit edilmemişti. `services replace` **declarative**; elle yapılan her müdahale kaynağa yazılmazsa sessizce geri alınır.

### Deploy sonrası doğrulama

```bash
gcloud run services describe jarvis-voice --project your-gcp-project --region europe-west1 \
  --format="value(status.traffic[0].revisionName,status.traffic[0].percent)"
curl -s -o /dev/null -w "%{http_code}\n" https://jarvis-voice-xxxxxxxxxx-ew.a.run.app/api/health
# CM warmup gerçekten koştu mu (yeni revizyon adıyla):
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="jarvis-voice" AND resource.labels.revision_name="<yeni-rev>"' \
  --project your-gcp-project --limit 300 --format="value(timestamp,textPayload)" --order=asc | grep -iE "warmup|CM: torch"
```
Beklenen: `warmup: cm model loaded` **ve** `CM: torch threads pinned to 2`. Brain'de `cm model loaded` **görünmemeli** (3Gi'ye sığmaz, bilerek kapalı).

## 9. Sesli tur test prosedürü (Kadir'in istediği)

Kadir cihazı kendi sürer; ajan **dokunmaz**. Ajanın işi: kur, logu temizle, sonra oku.

```bash
# 1. Bağlan (port her açılışta değişir — Kadir'den iste)
adb connect 100.64.0.10:<port>
# 2. Kur ve logu temizle
cd /home/user/Projeler/JARVIS/android
JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:assembleDebug
adb -s 100.64.0.10:<port> install -r app/build/outputs/apk/debug/app-debug.apk
adb -s 100.64.0.10:<port> logcat -c
```

**Kadir yapar:** uygulamayı aç → mikrofon → normal sesle 4-5 saniyelik bir cümle → cevabı hoparlörden dinle. Rapor eder: duyuldu mu, hoparlör mü kulaklık mı, seviye normal mi.

**Ajan okur:**

```bash
# İstemci: AEC zinciri gerçekten bağlı mı
adb -s 100.64.0.10:<port> logcat -d | grep AndroidMicSource
# Sunucu: kimlik + CM
gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="jarvis-voice" AND timestamp>="<UTC>"' \
  --project your-gcp-project --limit 300 --format="value(timestamp,textPayload)" --order=asc \
  | grep -E "CM:|voice trust|speaker.identify|Memory limit"
```

Bakılacaklar: `aec=true` · `silenced` (true ise kendi TTS'imiz mikrofonu eziyor) · `cm_ok=True` · `p_fake` (0.01 altı sağlıklı) · `anchor_score` (0.60'ı geçerse galeri öğrenmeye başlamış demektir) · `Memory limit` satırı **hiç olmamalı**.

⚠️ **`setMode` regresyon nöbeti:** cevap kısık gelirse ya da kulaklıktan çıkarsa `fa2dafd` şüphelidir (bazı OEM'lerde `MODE_IN_COMMUNICATION` rotayı earpiece'e zorlar, seviyeyi çağrı akışına bağlar — 4 Ağustos "cevaplar sessiz" regresyonunun sınıfı). Pixel 10 Pro'da 11 Ağu 02:48'de rota **hoparlörde** kaldı, sorun görülmedi.

## 10. Tuzaklar — bu gece bedeli ödenenler

- **`connectedAndroidTest` bağlı TÜM cihazlarda koşar.** Kadir'in telefonu ve tableti adb'de duruyor. Her instrumented koşuyu `ANDROID_SERIAL=emulator-5554` ile sabitle. `--tests` bu AGP'de geçersiz; `-Pandroid.testInstrumentationRunnerArguments.class=...` kullan.
- **Subagent'lar çalışma ağacını kirletiyor.** Üç review'cı `git stash`/dosya değiştirme yaptı (hepsi geri aldı). Review dispatch'lerine "STRICTLY READ-ONLY, git durumunu değiştirme" yaz **ve** her review sonrası `git status --short` + `git stash list` ile doğrula.
- **Cihaz sürme Kadir'in.** Ajan yalnız derler, kurar, log okur. Tap/swipe/ayar yok.
- **Gradle bellek muhafızı:** her Gradle çağrısından önce `free -g`, 4 GB altındaysa koşma.
- **Plan şablonlarına güvenme.** Bu gece plandaki kod parçacıklarında üç hata çıktı: olmayan dataclass alanları (`VoiceSignals(verified=…, cm_fake_prob=…)`), olmayan metot adı (`_on_final_transcript`), ve `AndroidMicSource`'un olmayan `context`'i. Hepsi benim ezberden yazdığım satırlardı. **Dispatch'ten önce imzaları koddan doğrula.**

## 11. Bilerek kapsam dışı bırakılanlar

- **CM eşik kalibrasyonu (0.85).** Hem bonafide hem spoof dağılımı ister; spoof tarafı güçlü-klon (Chatterbox sınıfı) testine bağlı, o ayrı bir dilim. Bu gecenin `p_fake=0.0006` ölçümü tabloyu değiştirdi, ama tek yönlü veriyle eşik oynatmak tahmin yürütmektir.
- **Tek-`AudioRecord` → PFD yeniden yapımı.** Task 7'nin cevabına bağlı: Google'ın tr-TR tanıyıcısı PFD beslemesini kabul ediyor mu?
- **10 saniyelik tampon tavanı.** 10 Ağustos turunda `dur_in` üç kez tam `10.00s` çıktı — tesadüf değil, bir sınır. Bulunmalı, ama bu dilimi bloke etmiyor.
- **`npx` imajda yok** → `github_mcp` her turda bağlanamayıp yeniden deniyor, yani GitHub MCP aracı fiilen çalışmıyor ve her tur birkaç yüz ms yiyor. Ya imaja Node ekle ya da toolset'i kayıt defterinden çıkar.

## 12. Rekabet analizinden alınacaklar (sentez yapıldı, uygulanmadı)

Kaynak: `docs/arastirma/2026-08-05-ekosistem-analizi.md`. **§1'in tamamı OpenClaw ve mobil tarafın en somut malzemesi orada** — ilk sentezimde atlamıştım, Kadir düzeltti.

Sırayla: **D5(i)** saatte credential (yukarıdaki karar) · **D5(ii)** pahalı ses kapısından önce ucuz kanal filtresi (CM 2.2 sn + ECAPA ~1 sn ölçüldü, tanınmayan eşi oraya kadar getirmeye gerek yok) · **Ç6** iki kapılı yetenek modeli (istemci deklare eder + bulut allowlist'i izin verir) · **D4** Leon'un Satellite deseni (telefon = eylem yüzeyi; satellite düşünce o tool'lar model için "unavailable") · **D1** ZeroClaw tool receipts (maliyeti bir HMAC) · **D2** onay kartında kararın hangi katmandan geldiğini göster.

**Kod almıyoruz, tasarım alıyoruz:** OpenClaw'da 5.520 açık issue ve 65 sayfa güvenlik advisory'si var, ve yapısal kusuru "yetki her çağrı yolunda ayrı ayrı kontrol ediliyor" — kod alırsak o sınıfı da alırız.

## 13. Park edilmiş yön — Jarvis CLI / PC ikame

Kadir istedi, tasarım başladı, **kasten park edildi** (mobil bitsin diye). Alınan kararlar:

- Üç alt-proje: **S1** Jarvis CLI · **S2** Claude Code sürücüsü · **S3** PC'yi ikame olarak kullanma. Önerilen sıra S2 → S1 → S3.
- **Kim başlatır:** ikisi de — Kadir'in ağzından röle serbest, Jarvis'in kendi kararı §9 bölge matrisine tabi.
- **Hangi sohbet:** Jarvis'in kendi kalıcı Claude oturumu esas, canlı terminale yazma röle için.
- **Şeffaflık şartı:** Jarvis'in ürettiği oturumlar `~/.claude/projects/<proje>/<uuid>.jsonl` altında **normal oturum** olarak durmalı ki Kadir eklentiden görüp `--resume` edebilsin. Bu, ayrı bir protokol yazmayı gereksiz kılıyor.
- **Mekanizma zaten var:** Antigravity IDE eklentisi Claude Code'u tam da bu şekilde sürüyor — `claude --output-format stream-json --input-format stream-json --resume=<id> --replay-user-messages`.
- **Taşıma:** Tailscale **değil**. İlke 7 outbound-only; PC de Pi gibi Pub/Sub'dan çeker. (Tailscale bizim geliştirme aracımız, Jarvis'in taşıma katmanı değil.)

## 14. Standing kurallar (bu gece Kadir'in koyduğu)

- **Mikro adım yok.** Dilim planla, uçtan uca bitir, sıralamayı kendin yap. "Hangisinden devam edelim?" ile biten mesaj neredeyse her zaman yanlış mesaj.
- **YZ maliyeti hep mevcut abonelikten.** Token-başına ücretli API önerme — bu kural gece bir tavsiyeyi (Vertex `gemini-embedding-001`) çöpe attı. Jarvis'in beyni zaten `llm-proxy` sidecar'ı üzerinden abonelik kotasından yiyor.
