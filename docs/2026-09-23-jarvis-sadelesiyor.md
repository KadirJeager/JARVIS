# JARVIS sadeleşiyor

Tarih: 2026-09-23

Durum: Kullanıcının ileride projeye dönerken ele alınmak üzere kaydedilmesini istediği mimari yön ve değerlendirme. Bu belge kurulum veya geçiş yapıldığı anlamına gelmez. Uygulamaya sonraki proje çalışmasında başlanacak; entegrasyon ayrıntıları o zaman doğrulanacak.

## Amaç ve öncelik

Tek kişinin sürdürebileceği, ayrı Android/PWA ve yardımcı uygulama bakımını azaltan kişisel asistan. Kullanıcı Google Chat üzerinden JARVIS ile konuşacak; sistem işi anlayacak, uygun ajanı görevlendirecek, sonuçları doğrulayacak ve baştan sona takip edecek.

Bu yön, eski özel istemci geliştirme hedeflerinden önceliklidir. Eski North Star ve planlardaki Android/PWA, ses ve onay kartı varsayımları yeni işe başlamadan önce bu belgeyle karşılaştırılmalı; yalnız geçmiş planda var diye sürdürülmemeli. Mevcut uygulamaları silme veya servisleri kapatma kararı alınmadı.

## Hedef görev paylaşımı

| Bileşen | Sorumluluk |
| --- | --- |
| Google Chat | Kullanıcı iletişimi, ilerleme, sorular ve gerekli onaylar |
| Buluttaki JARVIS | Kişisel kimlik/bağlam ve model sağlayıcısı; PC kapalıyken mesaj karşılama bağlantısı |
| PC'deki Hermes | Çalışma döngüsü, araçlar, işçi ajan oturumları ve sonuç takibi |
| Gemini CLI / Claude Code / Codex | Projelerde gerçek uygulama ve doğrulama işleri |
| Ortak Obsidian kasası | Kalıcı tercihler, proje bilgileri ve kararların esas kaydı |
| Tuya PCIe kart | Gerektiğinde PC açılışı; JARVIS entegrasyonu henüz doğrulanmadı |
| Google Meet / Moonlight | Ekran gösterme veya uzaktan kullanım |

Kullanıcının açık tercihi: Hermes, **JARVIS'i adres + API anahtarıyla sağlayıcı olarak kullanacak**. JARVIS kimliği Hermes üzerinde yeniden kurulmayacak. Sağlayıcı arkasında bugün çalışan CLIProxyAPI yolu ilk deney yoludur; 2026-09-24 düzeltmesine göre Google abonelik kotasına sürdürülebilir başka erişim varsa CLIProxyAPI zorunlu değildir.

Google/Gemini aboneliği genellikle sürekli; Claude ve Codex dönemsel. Model sağlayıcısının abonelik erişimiyle gerçek CLI ajanlarının oturum ve kota durumları ayrı değerlendirilmelidir. Her işi bütün ajanlara göndermek yerine kullanılabilir uygun ajan seçilmeli.

## Teknik tasarım önerisi

- JARVIS'e ayrı bir sağlayıcı modu eklenmeli. Hermes'in mesajları, araç tanımları, araç çağrıları ve sonuçları korunmalı; akış desteği sınanmalı. JARVIS kişisel bağlamla karar üretirken çalışma döngüsünü Hermes yürütmeli.
- Mevcut tam ADK ajan döngüsü yalnız metin cevabı veren bir sarmalayıcıyla Hermes'in model sağlayıcısı yapılmamalı. İki yöneticinin aynı işi planlaması, araçları gizlemesi ve geçmişi çoğaltması önlenmeli.
- Mevcut JARVIS MCP araçları yeniden kullanılmalı. Kodda `/mcp/` üzerinden profil/hafıza/repo araçları var; canlı erişim doğrulanmadı. Mevcut Google ID token doğrulaması yeni sağlayıcı anahtarıyla otomatik uyumlu değildir.
- Cloud Run'da küçük bir mesaj karşılama ve kalıcı iş kaydı katmanı düşünülebilir: mesajı kaydet, gerekirse PC'yi aç, gerçekten hazır olduğunu kontrol et ve işi teslim et. Uzun işi tek HTTP isteğine bağlama. Mesaj alımının tek sahibi olsun; bulut ve Hermes aynı mesajı yarışarak veya iki kez yürütmesin.
- Ortak hafıza esas kaynak; bulut kopyası kullanılıyorsa güncellik zamanı görünür olmalı. Sohbet oturumu ve iş durumu kalıcı kullanıcı bilgisinden ayrı tutulmalı. Firestore/Obsidian/Hermes arasında çelişen üç esas hafıza oluşturulmamalı.
- JARVIS mimarisini, yetkilerini ve gerçek araçlarını bilmeli. Planlanan / yapılandırılan / test edilip çalışan yetenekler ayrılmalı. PC, ajan, model ve masaüstü erişimi zaman damgalı durum kontrolleriyle anlaşılmalı; görevlendirilen ajanlara ilgili bağlam ve başarı ölçütü verilmeli.
- Hermes çalışırken kendi ayarlarını işçi ajanlara düzelttirebilir. Tamamen açılmama durumunda bağımsız servis kurtarma, çalışan yapılandırmaya dönüş ve mevcut Tailscale erişimi korunmalı.

## Kazançlar, maliyetler ve alternatifler

Kazanç: özel istemci bakımı azalır; hazır Hermes altyapısı kullanılır; JARVIS kimliği ve sağlayıcı sınırı korunur; abonelik/işçi değişimi bütün sistemi yeniden kurmayı gerektirmez.

Maliyet: sağlayıcı uyumu, Chat bağlantısı, bulut–PC iş teslimi, kimlik doğrulama ve hata kurtarma hâlâ entegrasyon ister. API anahtarı tek başına bunları çözmez. Mevcut özel istemci onay kartlarının Chat'e uyarlanması gerekir.

Google Chat'in belgelenen Hermes kurulumu Workspace ve uygulama yayımlama yetkisi gerektiriyor; yalnız Gmail hesabıyla kurulabildiği varsayılmamalı. Hazır bağlantı Pub/Sub pull kullanıyor; PC kapalıyken bulut karşılama tasarımıyla birlikte ele alınmalı. Hesap uygunluğu uygulama öncesi doğrulanmalı.

Cloud Run istek karşılamaya uygun; sürekli çalışan Hermes sürecini buraya taşımak ayrı kaynak/faturalandırma ayarları gerektirir. PC kapalıyken kapsamlı iş yürütme önemli hâle gelirse sürekli açık küçük sunucuda Hermes alternatif olabilir; ek sunucu ve uzaktan PC araçları bakımını getirir.

Hermes'in doğrudan proxy kullanıp JARVIS'e yalnız MCP araçları için bağlanması daha az sağlayıcı kodu gerektirebilir, ancak kullanıcının JARVIS'i sağlayıcı yapma tercihine uymaz. Her şeyi özel JARVIS orkestratöründe geliştirmek ise azaltılmak istenen bakım yükünü büyütür. Bu alternatiflere geçiş kararı yok.

## Sonraki çalışmanın başlangıcı ve sırası

1. Bu belgeyi, ortak hafızadaki JARVIS notunu ve mevcut kaynak kodu yeniden değerlendir; canlı servis, hesap ve ajan durumunu doğrula.
2. Küçük bir sağlayıcı deneyi yap: Hermes → JARVIS → araç çağrısı → gerçek araç sonucu → nihai cevap. Mesaj/araç kimlikleri ve akış doğru kalmalı.
3. İlk uçtan uca başarı: Chat'ten verilen sınırlı bir proje işini uygun CLI ajanı yapmalı, test sonucu alınmalı ve aynı konuşmaya raporlanmalı. Dur/iptal, gerekli soru/onay, bağlantı kesilmesi ve yeniden başlatmada işi iki kez çalıştırmama davranışları doğrulanmalı.
4. Tuya açılışı, PC hazır kontrolü ve bekleyen işe devam etme eklenmeli. Güç düğmesini körlemesine tekrar tetiklememeli.
5. Meet üzerinden PC ekranını paylaşma ayrıca denenmeli. Kullanıcının kastı JARVIS'in kendi kullandığı PC ekranını göstermesi; telefon ekranını modele aktarmak değil. Masaüstü/tarayıcı izinlerinin otomatik çalıştığı varsayılmamalı.

## İnceleme dayanakları

- Yerel kaynaklar: `brain/app/main.py`, `brain/app/agent.py`, `brain/app/guest_gate.py`, `brain/README.md`.
- [Hermes özel sağlayıcılar](https://hermes-agent.nousresearch.com/docs/integrations/providers)
- [Hermes Google Chat](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/google_chat)
- [Hermes MCP](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)
- [Cloud Run arka plan çalışması](https://docs.cloud.google.com/run/docs/tips/general)

Belgeler 2026-09-23 tarihinde incelendi; uygulama sırasında değişebilen koşullar yeniden doğrulanmalı. Bu kayıtta anahtar veya başka sır bulunmaz.
