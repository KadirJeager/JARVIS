# JARVIS canlı sadeleştirme envanteri

Tarih: 2026-09-24. Kaynaklar: Google Cloud Console'da salt okunur kontrol, mevcut depo kodu ve [harness yönetimi planı](2026-09-24-harness-yonetim-plani.md). Bu belge canlıda yapılmış değişiklik iddiası değildir.

## Bugünkü dağıtım

| Yüzey | Canlı kanıt | Yeni yön |
| --- | --- | --- |
| Cloud Run | `your-gcp-project/europe-west1` içinde yalnız `jarvis-brain` ve `jarvis-voice` var. İkisi de min instance `0`, istek bazlı faturalandırma, en çok 20 instance ve 80 eşzamanlı istek. Brain trafiği `jarvis-brain-00041-k8x` (27 Ağustos), voice trafiği `jarvis-voice-00035-bpq` (2 Eylül) revizyonunda. | Tek küçük kontrol yüzeyi; ses servisi yeni kanal doğrulandıktan sonra kapatılacak. |
| İmaj ve model yolu | Brain ana kapsayıcı 3 GiB; voice 4 GiB. Voice, brain ile aynı `cm-e42bde1` imajını kullanıyor. Her iki serviste `llm-proxy` yan kapsayıcısı var. Brain `JARVIS_LLM_BASE_URL=http://localhost:8317`; proxy Antigravity OAuth sırrını mount ediyor. | Ses/anti-spoof modelleri ve kullanılmayan MCP kodu çıkarılacak. **Çalışan CLIProxyAPI yan kapsayıcısı** ince kontrol servisinde yeniden kullanılacak; Hermes model istekleri JARVIS API sınırından ona gidecek. |
| HTTP ve istemci | Repo `brain/app/main.py` içinde ADK sohbet, PWA, Android/Wear konuşma/geçmiş/onay, cihaz token/FCM, ses WebSocket ve enrollment, MCP uçları aynı uygulamada. Cloud Run erişiminde `allUsers` grant'i var; uygulama düzeyindeki kimlik denetimi ayrıca çalışıyor. | Google Chat ana kumanda kanalı; Workspace, e-posta, Telegram, WhatsApp ve mümkünse Google Mesajlar erişimleri yeni görev sözleşmesine bağlanacak. Eski özel Android/PWA/Wear istemci uçları yeni akış doğrulandıktan sonra kaldırılacak. Android cihazın görev aracı olarak kullanımı korunacak. |
| Kalıcı veri | Firestore `(default)` Ready, Standard/Native, `eur3`; 21 kök koleksiyon var. Eski konuşma/görev/onay/ses kayıtları duruyor. Scheduled backups kapalı. | Yeni görevler ayrı `harness_tasks_v1` koleksiyonunda. Eski veriye geçiş sırasında dokunulmayacak; ileride saklama/silme kararı ayrıca veri envanterine dayanacak. |
| Zamanlayıcı | Altı eski Scheduler işi (`approvals-tick`, `reminders-tick`, `repo-watch`, `task-tick`, `weekly-retro`, `workspace-poll`) PAUSED; konsol “Has not run yet” gösteriyor. Cloud Run Jobs yok. | Yeni akış bu işlere bağımlı olmayacak; kapanışta hedef ve tetikleyiciler temizlenecek. |
| Sırlar | Secret Manager'da eski proxy, Google API ve Workspace sırlarının adları görülüyor; değerleri okunmadı. | Kullanılmayan sırlar ancak aktif revizyon ve veri bağımlılığı doğrulandıktan sonra kaldırılacak. AI Studio anahtarı yeni model yolu değildir. |
| Google Home API | Projenin Enabled APIs listesinde yok. `home.googleapis.com` Library sayfası **Enable** gösteriyor; etkinleştirme yapılmadı. | Projeye eklenmesi mümkün, ancak Cloud Run'dan Tuya kartına belgelenmiş doğrudan Home APIs komut yolu doğrulanmadan entegrasyon sayılmayacak. |

## Yeni ince çekirdek: gerçek sınır

Depoda `brain/app/harness_control.py`, `harness_tasks.py`, `pc_connector.py`, `hermes_model_api.py` ve `Dockerfile.harness-control` ile ayrı, küçük kontrol uygulaması hazırlanmış durumda. Görev oluşturma, PC kuyruğu, tek seferlik claim ve sonuç yazma yerel testlerde çalıştı. `POST /v1/chat/events` Chat isteğini Google kimliği ve kullanıcı izin listesiyle denetleyip `message.name` üzerinden tek görev oluşturuyor; terminal sonuç, kalıcı görev kaydından sonra Chat'e sabit `requestId` ile gönderiliyor. PC bağlayıcısı Hermes Runs API'ye idempotent görev devri, yeniden başlatma sonrası güvenli uzlaştırma ve gönderim başarısızlığında sonucu günlükte tutma yolunu içeriyor. `/v1/chat/completions` mevcut CLIProxyAPI için ayrı kimlikli model sınırıdır. Ayrı `tuya_power.py` Tuya cihaz işlevi/durumu okumayı ve açıkça seçilmiş tek komutu destekliyor; otomatik PC açma kararını vermiyor. Bu ekler sahte servislerle yerel test edildi. **Canlı Firestore, gerçek Google Chat, gerçek CLIProxyAPI araç turu, Tuya kartı, PC açma ve Hermes teslimatı doğrulanmadı.** Uygulama bugün görev defteri/teslimat yüzeyi; PC kapalıyken model gerektiren işleri kendi başına bitiren bir bulut işçisi değil.

Hedef çekirdeğin görevleri: Google Chat olayını doğrulayıp kalıcı kaydetmek, görevi yönlendirmek, güç/harness durumunu izlemek, sonucu aynı konuşmaya yazmak ve PC kapalıyken yapılabilen hafif işleri yürütmek. Yerel kodda sahibin düz `durum`/`status` mesajı Firestore'dan aktif görev sayılarını PC yöneticisi başlatmadan hesaplayabiliyor; gerçek Chat/Cloud Run bağlantısı henüz sınanmadı. Uzun model/terminal işi istek ömrüne bağlanmayacak. PC kapalıyken bağımsız iş kapasitesinin kapsamı, ayrı ücretli model API kullanmadan çalışan gerçek sağlayıcı veya mevcut araç kanıtına göre belirlenecek; sırf kuyruğa almak “PC'siz iş yapıyor” diye sunulmayacak.

## Kesim sırası

1. İnce kontrol imajını eski brain/voice dağıtımından bağımsız kur; gerçek Firestore'da tekillik, görev durumu, kimlik doğrulama ve sonuç kaydını doğrula. Eski 21 koleksiyona müdahale etme.
2. Google Chat giriş ve aynı konuşmaya cevap akışını gerçek hesapta doğrula. Chat kullanılana kadar eski kullanıcı kanalını kapatma.
3. PC güç yolu, açılış/kalp atışı, Hermes hizmeti, abonelikli model yolu ve terminal/Computer Use görevini gerçek cihazda uçtan uca doğrula. Google Home API ile MCP koşullarını karıştırma; Home API proje etkinleştirmesi tek başına Cloud Run cihaz komutu kanıtı değildir.
4. PC kapalıyken en az bir gerçek hafif görevi baştan sona tamamla; model gerektiren görevler için mevcut CLIProxyAPI üzerinden bulut işi veya PC açma kuralını doğrula.
5. Chat ve ilk bulut Gmail işi ile PC/Hermes teslimatı canlı çalışınca trafiği yeni akışa taşı. Ardından aşağıdaki kaldırma matrisiyle eski servis, kod ve dağıtım bağımlılıklarını tek tek çıkar; her kesimde sağlık, Chat sonucu, PC teslimatı ve ilgili Workspace işini yeniden doğrula. Çalışan CLIProxyAPI yolu korunacak. Veri saklama kararı ayrı verilecek.

## Kaldırma matrisi — 2026-09-25 kullanıcı isteği

| Aday fazlalık | Somut yüzey | Kaldırma koşulu |
| --- | --- | --- |
| Eski ses servisi ve ses modelleri | Canlı `jarvis-voice`; `brain/Dockerfile` içindeki CPU torch/torchaudio, SpeechBrain ECAPA, anti-spoof model indirmeleri; `brain/app/voice*.py`, `speaker*.py`, `antispoof.py`, `vertex_stt.py` ve ses deney betikleri | Chat/PC akışı canlı, yeni ekran paylaşımı/arama ihtiyacı eski ses servisine dayanmıyor; ardından servis ve kullanılmayan model katmanları kaldırılır. |
| Eski ADK ve özel web/telefon istemcisi | `brain/app/agent.py`, `brain/app/main.py` içindeki `/api/chat`, `/ws/voice`, onay/geçmiş/FCM/cihaz uçları; `brain/web/`, `android/app/`, `android/wear/` ve bunlara özgü bağımlılıklar | Yeni Chat komutu, onay/soru, görev ve sonuç akışı gerçek kullanıcıyla doğrulanır. Android telefonun ADB/uygulama aracı olarak kullanımı ayrı tutulur. |
| Eski MCP ve ajan araç katmanı | `brain/app/guest_gate.py`, `tool_registry.py`, eski GitHub MCP sunucusu için `brain/Dockerfile` Node/npm katmanı | Hermes'in ihtiyaç duyduğu hafıza/repo/araç işlevleri yeni yoldan doğrulanır; yalnız kullanılmayan sunucu katmanı çıkarılır. |
| Eski Scheduler iş döngüsü | Altı PAUSED Cloud Scheduler işi; `brain/app/tasks.py`, `events.py`, `reminders.py`, `repo_watch.py`, `workspace_poll.py` ve `/api/jobs/*` | Yeni kalıcı görev, hatırlatma/izleme ve OAuth yenileme ihtiyacı için karşılıklar belirlenir; kullanılmayan tetikleyiciler ve kod kaldırılır. |
| Ağır embedding ve eski imaj | `brain/Dockerfile` içindeki 1.1 GB e5 model indirmesi; eski brain/voice ortak büyük imajı | Ortak hafızanın yeni okuma/yazma/arama yolu canlı doğrulanır; yeni ince imajın bu ağırlıklara bağımlı olmadığı test edilir. |
| Eski sırlar, OAuth istemcileri ve veri | Kullanılmayan Secret Manager sürümleri, eski Android debug OAuth istemcisi, 21 kök Firestore koleksiyonu, eski konteyner imajları | Aktif revizyon/kanal bağımlılığı ve saklama yükümlülüğü çıkarılır; kullanıcı verisinin ne kadar tutulacağı belirlenir; yedek ve geri dönüş noktası doğrulanır. Sonra kullanılmayan kaynaklar kaldırılır. |

Kesim her yüzey için `bağımlılık → yeni karşılık → canlı kanıt → kaldırma → yeniden doğrulama` kaydıyla yapılır. Eski `jarvis-brain` ancak yeni ince kontrol, bulut işi, Hermes model yolu ve Chat sonucu çalıştıktan sonra devreden çıkarılır. **CLIProxyAPI yan kapsayıcısı, abonelik OAuth yolu, yeni görev defteri ve ortak hafıza erişimi korunur.**

Bu sırada `jarvis-brain` ve `jarvis-voice` için canlı dağıtım veya silme yapılmadı.
