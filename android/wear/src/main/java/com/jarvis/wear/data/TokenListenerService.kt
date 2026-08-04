package com.jarvis.wear.data

import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import com.jarvis.wear.BuildConfig
import com.jarvis.wear.WearApp
import kotlinx.coroutines.runBlocking

/**
 * Telefondan gelen kalıcı cihaz token'ını alır ve şifreli depoya yazar (tek seferlik
 * eşleştirme akışı — spec §5). [MESSAGE_PATH] telefonun
 * `com.jarvis.data.wear.WatchPairing.TOKEN_PATH` sözleşmesiyle birebir aynı olmak
 * ZORUNDA — Task 4 review fix: artık ikisi de aynı tek kaynaktan (her modülün kendi
 * `BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH`'ı, ikisi de tek bir
 * `android/gradle.properties#jarvis.wearDeviceTokenMessagePath` değerinden üretilir)
 * okur — iki elle yazılmış ayrı literal DEĞİL. `:wear` ve `:app` yine de birbirine
 * doğrudan bağımlı değil (ayrı APK'lar, ayrı cihazlar); ortak nokta yalnız bu tek Gradle
 * property'si.
 * [TokenListenerServiceTest][com.jarvis.wear.data.TokenListenerServiceTest] hem bu
 * literalin beklenen değerde olduğunu hem de servisin gerçekten paylaşılan kaynağı
 * kullandığını (ayrı bir literale geri dönmediğini) pinler.
 *
 * Token asla loglanmaz — geçersiz yük ya da depoya yazma hatası durumunda bile yalnız
 * genel bir uyarı yazılır, içeriği değil (telefonun
 * [com.jarvis.data.wear.WatchPairing]'iyle aynı disiplin).
 */
class TokenListenerService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != MESSAGE_PATH) return
        val token = String(event.data, Charsets.UTF_8)
        if (!token.startsWith(TOKEN_PREFIX)) {
            Log.w(LOG_TAG, "bilinmeyen eşleştirme yükü görmezden gelindi") // token ASLA loglanmaz
            return
        }
        try {
            runBlocking { (application as WearApp).tokenStore.save(token) }
        } catch (e: Exception) {
            // Keystore/DataStore yazımı başarısız olabilir (anahtar yenilendi, disk
            // hatası, vb.) — dinleyici SÜRECİ ÇÖKMEMELİ; hata mesajı burada da token
            // içeriğini taşımaz.
            Log.w(LOG_TAG, "eşleştirme token'ı depoya yazılamadı", e)
        }
    }

    companion object {
        /** Task 4 review fix: `:app`/`:wear` ortak `BuildConfig` alanından okunur —
         * artık iki elle yazılmış literal DEĞİL, tek kaynak. */
        val MESSAGE_PATH: String = BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH
        private const val TOKEN_PREFIX = "jdt_"
        private const val LOG_TAG = "TokenListenerService"
    }
}
