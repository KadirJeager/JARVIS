# K0 — gerçek sağlayıcı ve seçmeli araç deneyi

Bu dizin üretim çekirdeği değildir. Pydantic AI'ın hazır araç döngüsü ve
`ToolSearch` desteğini, TypeSafe/Jev'in yapılandırılmış seçimiyle gerçek bir
sağlayıcı üzerinde sınar. Sahte model, sabit araç sonucu, ücretli API yedeği veya
otomatik model adı seçimi içermez. Sürümler `uv.lock` içinde sabitlenmiştir.

## Çalıştırma

Python 3.12+ ve uv gerekir. Bağlantı bilgileri yalnız ortamdan alınır:

- `JARVIS_K0_BASE_URL`: gerçek proxy kök adresi; HTTPS veya yerel HTTP origin.
- `JARVIS_K0_PROXY_KEY`: bu proxy'nin erişim anahtarı; Google AI Studio anahtarı değil.
  Google protokolünde zorunludur; kimlik doğrulaması kapalı yerel OpenAI-uyumlu
  sunucuda (ör. loopback'teki LM Studio) boş bırakılabilir.
- `JARVIS_K0_MODEL`: aynı bağlantının canlı kataloğundan seçilmiş model kimliği.
- `TYPESAFE_API_KEY`: mevcut TypeSafe anahtarı.
- İsteğe bağlı `PYDANTIC_AI_NO_BANNER=1`: SDK tanıtım çıktısını kapatır.

Önce kataloğu oku:

```bash
uv run --locked --project experiments/k0 python experiments/k0/probe.py catalog \
  --report /tmp/jarvis-k0-catalog.json
```

Gerçek paket adı `--package` ile verilir. Aşağıdaki değişkeni araştırılan paketin
adıyla ayarla; rapor yolu sır içermez:

```bash
uv run --locked --project experiments/k0 python experiments/k0/probe.py run \
  --package "$K0_PACKAGE" --report /tmp/jarvis-k0-google.json
```

`--protocol openai` aynı proxy'nin OpenAI Chat Completions protokolünü ayrı
deney olarak sınar; başka sağlayıcıya otomatik geçiş yapmaz. `--jev-model` ile
TypeSafe sürümü seçilebilir; varsayılan resmî `jev-latest` alias'ıdır ve cevapta
gerçekte kullanılan sürüm kaydedilir.

Bağımsız Jev araç seçimini incelemek için `select --query "$K0_QUERY"` modu
vardır. Bu modun başarılı olması Google araç turunun geçtiği anlamına gelmez.

`run --history <dosya>` konuşma geçmişini 600 izinli dosyaya yazar;
`resume --history <dosya>` onu yeni süreçte yükleyip kaynağı yeniden okumadan
cevap verdirir. Geçmiş sağlayıcı imzaları içerebilir; depoya konmaz, deneyden
sonra silinir.

`vision --image <görsel> --expect <kod>` araçtan dönen görselin modele
iletilmesini sınar. Görsel yalnız ertelenmiş `read_attached_image` aracıyla
modele ulaşır; beklenen kod metin olarak hiç gönderilmez. Her çalıştırmada
yeni rastgele kod üretilip görsele yazılmalıdır; sabit kodla tekrar kullanım
kanıt sayılmaz.

## Gerçek geçme ölçütleri

1. Başlangıçtaki model isteğinde yalnız `search_tools` bulunur.
2. Jev gerçek araç tanımlarından ilgili aracı seçer; eşleşme yok seçeneği vardır.
3. Açılan araç PyPI'den istenen paketin güncel verisini gerçekten okur.
4. Model araç sonucunu içeren bir devam isteği alır.
5. Son cevap okunan sürüm, kaynak URL ve yanıtın hesaplanmış SHA256 değerini içerir.

Rapor model isteklerindeki araç isimlerini, sonuç taşıma bilgisini, istek
gövdesindeki görsel/dosya parça sayısını, gerçek kaynak verisini, Jev seçimini
ve token sayısını kaydeder. Anahtarlar, OAuth içeriği,
modelin düşünce metni ve düşünce imzasının kendisi kaydedilmez. Beklenmeyen SDK
hatalarının yalnız türü ve varsa HTTP kodu raporlanır; ham hata gövdesi yazılmaz.

Bu dar deney; Cloud Run dağıtımını, PC gerçekten kapalıyken çalışmayı, kalıcı
vault'u, yeniden başlatmadan toparlanmayı, çok kullanıcılı yetkileri veya PWA'yı
kanıtlamaz. Bunlar sonraki kabul kapılarıdır. Gerçek sonuçlar tarihli K0 kanıt
belgesinde tutulur; çalıştırılmamış bir protokol bu README nedeniyle desteklenmiş
sayılmaz.

## İncelenen resmî kaynaklar

- [Pydantic AI ToolSearch](https://pydantic.dev/docs/ai/capabilities/tool-search/)
- [Pydantic AI TypeSafe/Jev](https://pydantic.dev/docs/ai/models/typesafe/)
- [Pydantic AI Google provider](https://pydantic.dev/docs/ai/models/google/)
- [TypeSafe API](https://docs.typesafe.ai/api)
- [Kalıcı yürütme ve konuşma saklama ayrımı](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)
