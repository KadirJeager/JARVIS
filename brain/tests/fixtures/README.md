# Speaker-ID fixtures — Task 1 gate sonucu

Bu dizin, "Katman 2b Dilim 3a — Kadir ses-kimliği" planının **Task 1** (bağımlılık +
ortam + ağırlık gate'i) çıktısını belgeler. Şu an bir fixture dosyası yok (bu task
sadece kurulum/ölçüm gate'i); sonraki task'lar burada gerçek ses fixture'ları
ekleyecek.

## Kullanılan interpreter (ÖNEMLİ — sonraki task'lar bunu kullanmalı)

- **Python 3.12.13**, `brain/.venv-speaker` içinde.
- Prod ile birebir aynı minor sürüm (`brain/Dockerfile`: `python:3.12-slim`).
- **Neden 3.12, 3.14 değil:** `brain/.venv` (proje varsayılanı) Python 3.14.6. Prod
  container'ı 3.12 olduğu için gate'i prod'u yansıtan sürümde çalıştırmak gerekti.
  Ölçüm: `torch` aslında cp314 wheel'i de yayınlıyor (bkz. aşağıda), yani 3.14'te de
  kurulabilirdi — ama mesele wheel varlığı değil, **prod'un fiilen 3.12 çalışması**;
  bu gate'in ve sonraki ECAPA embed test'lerinin prod'u yansıtmayan bir yorumlayıcıda
  geçmesinin hiçbir kanıt değeri yok. Bu yüzden 3.12 zorunlu tutuldu.
- **Nasıl bulundu:** çıplak `python3.12` komutu PATH'te yok, ama `uv` bu makinede
  CPython 3.12.13'ü zaten indirmiş durumdaydı (`uv python list`). Elle interpreter
  kurmak yerine mevcut uv-yönetimli toolchain kullanıldı.
- **Yeniden oluşturma (taşınabilir, önerilen):**
  ```bash
  cd brain && uv venv --python 3.12 .venv-speaker
  ```
  (Bu task'ta fiilen kullanılan komut, aynı 3.12.13'e çözülen ham interpreter yoluydu:
  `/home/user/.local/share/uv/python/cpython-3.12-linux-x86_64-gnu/bin/python3.12 -m venv .venv-speaker`
  — `uv venv --python 3.12` doğrulanmış, taşınabilir eşdeğeridir.)
- `.venv-speaker/` commit edilmedi (gitignore kapsamında `.venv*` deseni zaten var
  varsayımıyla — bkz. "Notlar" altında doğrulama).

## Kurulum notu — CPU-only torch (brief'ten bilinçli sapma)

Brief'in Step 2 komutu düz `pip install -e ".[speaker]"` idi. Bunu birebir
çalıştırmak, Linux'ta PyPI'nin varsayılan CUDA-bağımlı `torch` wheel'ini çeker
(nvidia-cublas, nvidia-cudnn, cuda-toolkit vb. — birkaç GB). `jarvis-voice` Cloud Run
üzerinde GPU'suz çalışacağı için (bkz. design spec §"Nerede çalışır") bu, ağırlık
ölçümünü **prod'u yansıtmayan** ve yanıltıcı derecede şişirilmiş bir sayıya
çevirirdi — tam da bu task'ın önlemeye çalıştığı şey.

Bunun yerine iki adımda kuruldu:
```bash
.venv-speaker/bin/pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchaudio>=2.2"
.venv-speaker/bin/pip install -e ".[speaker]"
```
İlk komut CPU-only `+cpu` varyantını sabitliyor; ikinci komut (`[speaker]` extra)
`torch`/`torchaudio` zaten `>=2.2` şartını karşıladığı için onları yeniden kurmuyor,
sadece `speechbrain` + transitive bağımlılıklarını ekliyor. `pyproject.toml`'daki
`speaker` extra'sı brief'te istendiği gibi index-url'siz, sade bırakıldı (bu sadece
`.venv-speaker` kurulumunda uygulanan bir kurulum-zamanı seçimi, paket
tanımına sızmadı).

## Doğrulanan import yolu

```python
from speechbrain.inference.speaker import EncoderClassifier
```
Brief'teki varsayım doğru çıktı — birebir bu satır import edildi ve çalıştı,
alternatif bir yola gerek kalmadı.

## Ölçülen embedding boyutu

```
EMBED_SHAPE (1, 1, 192)
EMBED_DIM 192
```
Beklenen 192 ile birebir eşleşti (ECAPA-TDNN standart çıktısı). Sonraki task'lardaki
`SPEAKER_EMB_DIM` sabiti **192** olarak alınabilir, güncelleme gerekmiyor.

Çalıştırma: `cd brain && .venv-speaker/bin/python scripts/speaker_smoke.py`
(sentetik 220Hz sinüs PCM16 mono 16kHz, 2 saniye — gerçek ses değil, sadece
şekil/boyut doğrulaması için).

## Kurulan sürümler

| Paket | Sürüm |
|---|---|
| torch | 2.13.0+cpu |
| torchaudio | 2.11.0+cpu |
| speechbrain | 1.1.0 |
| Python | 3.12.13 |

## Ağırlık ölçümü (in-process/sidecar kararı için)

Byte-doğru ölçüm (`du -sbL`, sembolik linkler takip edilerek):

| Bileşen | Boyut |
|---|---|
| `torch` (CPU-only) | 746.7 MB |
| `torchaudio` (CPU-only) | 2.5 MB |
| `speechbrain` (paket kodu) | 7.7 MB |
| ECAPA model ağırlıkları (`spkrec-ecapa-voxceleb`, HF cache) | 89.0 MB |
| **Toplam (çekirdek speaker-stack)** | **~846 MB** |

Notlar:
- Brief'in Step 4 komutu (`du -sh ... /tmp/spkrec-ecapa`) sembolik linkleri takip
  etmiyor (HF hub cache'i `/tmp/spkrec-ecapa` altına symlink koyuyor) — o komutu
  birebir çalıştırınca yanıltıcı şekilde `20K` çıkıyor. Gerçek model ağırlığı
  `du -shL` (veya doğrudan `~/.cache/huggingface/hub/models--speechbrain--spkrec-ecapa-voxceleb`)
  ile **89 MB**.
  ölçüldü.
- 846 MB'a `speechbrain`'in küçük transitive bağımlılıkları (hyperpyyaml, joblib,
  sentencepiece — birkaç MB'lık) dahil değil; `numpy`/`scipy` gibi büyük ama
  jarvis-brain'in mevcut (speaker'sız) bağımlılık ağacında zaten kısmen bulunması
  muhtemel paketler de bu toplama dahil edilmedi (gerçek prod imaj artışı bu
  yüzden ~850 MB–1 GB bandında, ama tam rakam prod Dockerfile'a `[speaker]` extra'sı
  eklenip gerçek `docker build` ile ölçülene kadar kesinleşmez — bu task'ın kapsamı
  dışında).
- Referans: `python:3.12-slim` taban imajı ~130 MB; bu ekleme container'ı kabaca
  ~1 GB civarına taşır.

## In-process kararı

**GEÇTİ — in-process fizibıl.** Design spec'in ("2026-07-24-katman-2b-dilim3a-ses-kimligi-design.md",
§"Nerede çalışır") gerekçesi bu ölçümle doğrulandı:

- Kurulum prod Python'unda (3.12) sorunsuz çalıştı, ek bir sistem bağımlılığı
  (örn. libsndfile ayrıca kurulmadı, `soundfile` wheel'i statik libsndfile ile geldi)
  gerekmedi.
- Embedding boyutu (192) beklenenle birebir — sonraki task'lar için sabit.
- ~850 MB–1 GB container artışı **tek seferlik, warm-instance amortismanlı**
  bir maliyet: `jarvis-voice` zaten `min-instances=1` ile sıcak tutulacağı için
  (spec: sesli oturumda soğuk-başlangıç kötü UX olur), bu ağırlık cold-start'ı
  **kullanıcı başına değil, deploy başına** bir kere etkiler.
- Kaçış kapısı (spec §"Kaçış kapısı") hâlâ geçerli: modül sınırı (`app/speaker.py`)
  temiz olduğu için container ağırlığı/cold-start sonradan gerçek bir sorun
  çıkarırsa `jarvis-speaker` sidecar'ına çıkarmak trivial refactor olarak kalıyor.
  Şimdilik YAGNI — bu gate onu gerektirmediğini gösterdi.

## Diğer notlar

- `brain/.venv-speaker/` bu repoda **commit edilmedi** — sadece `pyproject.toml`,
  `scripts/speaker_smoke.py` ve bu README commit edildi (brief Step 6'daki `git add`
  zaten sadece bu üç dosyayı listeliyor). Kök `.gitignore` **sadece** tam eşleşen
  `.venv/`'i kapsıyordu, `.venv-speaker/` kapsam dışıydı — bu, ~1.8 GB'lık bir
  scratch venv'in ileride bir `git add -A`/`git add .` ile yanlışlıkla commit'e
  girme riski demek. Bu gate'in kendi çıktısının yarattığı bir boşluk olduğu için
  kök `.gitignore`'a `brain/.venv-speaker/` satırı eklendi (ayrı, küçük bir takip
  commit'i ile — brief'in Step 6 commit'ini birebir tutmak için ana commit'e
  karıştırılmadı).
- HF Hub indirmesi kimliksiz (`HF_TOKEN` yok) yapıldı; script "unauthenticated
  requests" uyarısı verdi ama rate-limit'e takılmadan tamamlandı (~8 saniye,
  model + torch toplamda önceden ısıtılmamış önbellekten).
