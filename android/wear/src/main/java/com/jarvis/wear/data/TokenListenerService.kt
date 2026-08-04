package com.jarvis.wear.data

import android.util.Log
import com.google.android.gms.wearable.MessageEvent
import com.google.android.gms.wearable.WearableListenerService
import com.jarvis.wear.WearApp
import kotlinx.coroutines.runBlocking

/**
 * Telefondan gelen kalıcı cihaz token'ını alır ve şifreli depoya yazar (tek seferlik
 * eşleştirme akışı — spec §5). [MESSAGE_PATH] telefonun
 * `com.jarvis.data.wear.WatchPairing.TOKEN_PATH` sözleşmesiyle birebir aynı olmalı;
 * `:wear` ve `:app` ayrı modüller olduğu için sabit paylaşılamaz —
 * [TokenListenerServiceTest][com.jarvis.wear.data.TokenListenerServiceTest] bu literal
 * üstünde pinler.
 *
 * Token asla loglanmaz — geçersiz yük durumunda bile yalnız genel bir uyarı yazılır,
 * içeriği değil (telefonun [com.jarvis.data.wear.WatchPairing]'iyle aynı disiplin).
 */
class TokenListenerService : WearableListenerService() {
    override fun onMessageReceived(event: MessageEvent) {
        if (event.path != MESSAGE_PATH) return
        val token = String(event.data, Charsets.UTF_8)
        if (!token.startsWith(TOKEN_PREFIX)) {
            Log.w(LOG_TAG, "bilinmeyen eşleştirme yükü görmezden gelindi") // token ASLA loglanmaz
            return
        }
        runBlocking { (application as WearApp).tokenStore.save(token) }
    }

    companion object {
        const val MESSAGE_PATH = "/jarvis/device-token"
        private const val TOKEN_PREFIX = "jdt_"
        private const val LOG_TAG = "TokenListenerService"
    }
}
