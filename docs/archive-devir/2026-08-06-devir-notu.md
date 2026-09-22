# Devir notu — 6 Ağustos 2026 (Kimi oturumu)

Oturum üç iş yaptı: (1) durum analizi + yapılacaklar listesi, (2) Claude'un özel hafızasında kalmış vizyon backlog'unun North Star'a kurtarılması, (3) ekosistem analizi §7.2 **madde 1'in (anti-spoofing + T0-T3) tamamen kapatılıp canlıya alınması**.

## Canlı durum

| | Değer |
|---|---|
| Dal | `feat/antispoof-cm` (W1'in üstüne, çünkü Faz B/C W1'in brain tarafındaki voice-identity-scope değişikliklerine bağımlı) |
| Son commit | `1cfcb10` chore(deploy) — öncesi `1b056d6` (ana özellik), `67fa808` (spike), `253311a` (docs) |
| İmaj | `gcr.io/your-gcp-project/jarvis-brain:cm-1b056d6` |
| `jarvis-voice` | `00026-zxj`, trafik %100, 3Gi'de OOM yok |
| `jarvis-brain` | `00034-j7h`, trafik %100 |
| Testler | brain **851 yeşil, 2 skip** (+26 bu oturumda) |
| Doğrulama | health 200, openapi **24 yol** (+`/api/voice/challenge`), authsuz challenge → 401 |

## Analiz #1 — ne kuruldu

- **CM kapısı** (`brain/app/antispoof.py`): MMS-300M AntiDeepfake (CC-BY-NC-SA-4.0), fairseq→HF remap %100, lazy singleton, `/opt/antispoof` build-time baked. Eşik `CM_REJECT_THRESHOLD=0.85` (spike ölçümü: gerçek ses P_fake<0.001, sentetik >0.998; 3sn@2thread 0.46s).
- **Ses hattı** (`voice.py:_verify_utterance`): CM, ASV'den ÖNCE koşar. Spoof reddi VEYA CM hatası galeri yazımını kilitler (`identify`'da `cm_ok is True` şartı) ama akışı asla kırmaz. `cm_ok` → `VoiceSignals` → politika katmanı. `evt_speaker`'a `cm_ok` alanı (None ise alan yok — eski istemci uyumlu).
- **T0-T3** (`config.TOOL_TIERS`, default t2 fail-closed; `policy._decide_voice`): aktif spoof kanıtında T1+ → confirm; kanıt yokluğunda GREEN T2+ MEDIUM/LOW trust'ta → confirm. **Metin kanalı davranışı birebir aynı** (fail-open felsefesi korundu: Kadir asla kilitlenmez).
- **Challenge-response** (`voice_challenge.py`): enrollment (T3) artık 4 haneli sesli kod ister — kod `jarvis_text` ile söylenir, kullanıcı tekrarlar, grant 5 dk Firestore'da (`voice_challenges`) brain↔voice servisleri arasında paylaşılır. Grant'siz enroll → 409.

## KANITLANMADI — sıradaki kişinin işi

1. **Gerçek cihaz ses E2E** — telefon yoktu. Telefon dönünce: canlı sesli turda CM'in gerçek mikrofon sesini bonafide saydığını ve loglarda `cm_fake_prob` görmek.
2. **Chatterbox-sınıfı güçlü klon testi** — spike gTTS ile yapıldı (zayıf spoof). Asıl tehdit modeli (5 sn'yle klon) ölçülmedi. Telefon dönünce ya da ayrı spike.
3. **W1 Task 7 (saat emülatör E2E)** — telefon gelene kadar donuk; dal `feat/wear-w1-core` merge bekliyor.

## Oturum kararları (Kadir)

- Asistan sesi Kadir'den **klonlanmayacak**; istenen ses örnekle konabilecek → PerTh filigranı (analiz #2) düşük önceliğe indi, self-spoof riski büyük ölçüde kapandı.
- Telefon-bağımlı her şey telefon gelene kadar donuk.
- Git akışı: kod önce, commit onayla sonra (bu oturumda commit+deploy onayı verildi, yapıldı).

## Vizyon backlog kurtarıldı (North Star §4.8)

Claude'un `~/.claude/.../memory/` altında tuttuğu 24-25 Tem vizyonu (canlı mod "telefon=beden", her türlü girdi/çıktı, jest/bakış, telefon kontrolü, çok-modlu kimlik, kişiselleştirilebilir ses) kanonik belgeye hiç girmemişti — 5 Ağu analizi bu yüzden göremedi. Hepsi `JARVIS_Proje_Belgesi.md` §4.8'e işlendi. `docs/JARVIS_Proje_Belgesi.md` bayat kopya olarak damgalandı (kanonik: repo kökü). Ders ledger'da: ajan hafızası ≠ proje belgesi.

## Sıradaki (analiz §7.2 sırası)

- **#3 galeri sertleştirme** (drift bütçesi + versiyonlu geri alma; marj bandı zaten var — 2-3 gün) — doğal devam, CM kapısı şimdi bunun önünde duruyor.
- Sonra #4 taint bayrağı, #5 hafıza regresyon seti, #6 Mem0 yazma kapısı…
- #2 PerTh ve #12 tam dupleks: sırasıyla düşük öncelik ve kilitli (CM şartı artık sağlanmış durumda — tam dupleks kilidi teknik olarak açılabilir ama güçlü-klon testi önce).

## Küçük notlar

- `YourDialer/` ve `jarvis-profil.md` hâlâ untracked (Kadir'in).
- NotebookLM MCP bu oturumda bir kez timeout verdi; kritik değildi, rapor yerel olduğu için geçildi.
- Deploy dersleri uygulandı: builds submit pipe'lanmadı, nonce bump ile taze revizyon, revizyon trafik doğrulaması yapıldı.
