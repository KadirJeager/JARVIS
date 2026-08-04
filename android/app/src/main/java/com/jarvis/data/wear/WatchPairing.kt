package com.jarvis.data.wear

/**
 * Saat eşleştirme (Wear W1, spec §5): telefon kalıcı bir cihaz token'ı basar ve Wearable
 * MessageClient ile bağlı saate iter. Köprü YALNIZ bu tek akışta kullanılır — kalıcı token
 * saatin telefon bağımlılığını bilinçli olarak yok eder (W0): saat token'ı bir kez alır,
 * kendi kimlik doğrulamasını bağımsızca yürütür.
 *
 * [mint], [listNodes] ve [sendTo] arayüz arkasına alınmıştır: gerçek uygulaması Retrofit
 * (`DeviceTokenApi`) ve Play Services Wearable (`NodeClient`/`MessageClient`) kullanır —
 * bkz. [com.jarvis.AppContainer.watchPairing] — ama bu sınıf hiçbir Android/Play Services
 * tipi bilmez, bu yüzden [WatchPairingTest] saf JVM üzerinde koşar.
 *
 * Basılan token BELLEKTE bile tutulmaz: [pair] içindeki tek yerel değişkenden ötesine
 * geçmez, hiçbir yere loglanmaz veya kalıcılaştırılmaz — basılır, gönderilir, düşürülür.
 */
sealed class PairResult {
    data class Sent(val nodeCount: Int) : PairResult()
    object NoWatch : PairResult()
    data class Failed(val userMessage: String) : PairResult()

    override fun equals(other: Any?) = this === other ||
        (this is Sent && other is Sent && nodeCount == other.nodeCount)
    override fun hashCode() = javaClass.hashCode()
}

class WatchPairing(
    private val mint: suspend (device: String) -> String,
    private val listNodes: suspend () -> List<String>,
    private val sendTo: suspend (nodeId: String, path: String, payload: ByteArray) -> Unit,
) {
    companion object {
        const val TOKEN_PATH = "/jarvis/device-token"
        const val DEVICE_LABEL = "watch-ultra"
    }

    /**
     * Önce bağlı düğümlere bakar (adım 1) — hiç yoksa token hiç BASILMAZ (adım 3, boşa
     * token israfı yok), sonra basar (adım 1) ve TÜM düğümlere gönderir (adım 2). Bir
     * düğüme gönderim başarısız olursa (düşmüş olabilir) diğerleri denenmeye devam eder;
     * hiçbiri ulaşmazsa bu da [PairResult.Failed] sayılır.
     */
    suspend fun pair(): PairResult {
        val nodes = try {
            listNodes()
        } catch (e: Exception) {
            return PairResult.Failed("Saate ulaşılamadı; saat bağlı mı?")
        }
        if (nodes.isEmpty()) return PairResult.NoWatch

        val token = try {
            mint(DEVICE_LABEL)
        } catch (e: Exception) {
            return PairResult.Failed("Token basılamadı; oturumun taze mi? Tekrar dene.")
        }

        val payload = token.toByteArray(Charsets.UTF_8)
        var sent = 0
        for (node in nodes) {
            try {
                sendTo(node, TOKEN_PATH, payload)
                sent++
            } catch (e: Exception) {
                // Düğüm gönderim sırasında düşmüş olabilir; kalan düğümlerle devam.
            }
        }
        return if (sent > 0) PairResult.Sent(sent)
        else PairResult.Failed("Saate gönderilemedi; saat bağlı mı?")
    }
}
