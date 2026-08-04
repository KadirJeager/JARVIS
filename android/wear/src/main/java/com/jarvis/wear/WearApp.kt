package com.jarvis.wear

import android.app.Application
import com.jarvis.wear.data.DataStorePrefs
import com.jarvis.wear.data.JarvisApi
import com.jarvis.wear.data.KeystoreTokenCipher
import com.jarvis.wear.data.Net
import com.jarvis.wear.data.TokenStore
import kotlinx.coroutines.runBlocking

/** Hand-rolled DI (telefonun `AppContainer`'ı ile aynı desende, ama W1 kapsamı için tek
 * dosyada): [tokenStore] ve [api] tekil örnekler — [MainActivity] kök yönlendirme için,
 * [TokenListenerService][com.jarvis.wear.data.TokenListenerService] eşleştirme yazımı için
 * bunları [Application]'dan okur. */
class WearApp : Application() {
    lateinit var tokenStore: TokenStore
        private set
    lateinit var api: JarvisApi
        private set

    override fun onCreate() {
        super.onCreate()
        tokenStore = TokenStore(DataStorePrefs(this), KeystoreTokenCipher())
        api = Net.buildApi(BASE_URL) { runBlocking { tokenStore.read() } }
        // runBlocking interceptor'da: OkHttp zaten worker thread'de çağırır,
        // main thread'e dokunmaz. (Telefonun AuthClient deseniyle aynı sınıf.)
    }

    companion object {
        const val BASE_URL = "https://jarvis-brain-000000000000.europe-west1.run.app/"
    }
}
