package com.jarvis.wear

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.wear.compose.material.MaterialTheme
import com.jarvis.wear.data.JarvisApi
import com.jarvis.wear.data.TokenStore
import com.jarvis.wear.ui.ChatScreen
import com.jarvis.wear.ui.ChatViewModel
import com.jarvis.wear.ui.PairScreen
import java.time.LocalDate

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as WearApp
        setContent { WearRoot(app.tokenStore, app.api) }
    }
}

/**
 * Kök ekran (spec §5): [tokenStore]'un `hasToken` akışı Compose state olarak izlenir —
 * [TokenListenerService][com.jarvis.wear.data.TokenListenerService] telefondan token'ı
 * yazdığı AN bu ekran kendiliğinden Sohbet'e geçer; yeniden başlatma ya da manuel
 * yenileme beklemez (4 Ağu overlay dersi sınıfı: state'i olay değil, akışı sür).
 *
 * `initial = null` (Task 4 review fix): DataStore'un ilk okuması ASENKRON bir dosya
 * G/Ç'sidir; o yanıt gelene kadar `false` ile başlamak "kontrol ettim, token yok"
 * yalanını söyler ve zaten eşleştirilmiş bir saatte bile HER açılışta görülebilir bir
 * yanlış-ekran ("Saat eşleştirilmemiş") flaşına yol açar. `null` → [Route.Unknown]:
 * nötr/boş bir yüzey — ne Pair, ne de işin sürdüğünü ima eden yanıltıcı bir spinner.
 *
 * Task 5 review fix: [Route.Chat] kolu artık gerçek [ChatScreen]'i barındırır. 401 (token
 * geçersiz/iptal, bkz. [ChatViewModel.state]'in `needsPairing`'i) burada [TokenStore.clear]
 * çağrısına bağlanır -- bilinçli olarak [ChatViewModel] İÇİNDE değil: ViewModel Android'siz
 * (JVM testli) kalsın diye, "sakladığı token'ı silme" kararı Android tarafında (burada)
 * verilir. `clear()` sonrası `hasToken` akışı `false`'a düşer, `rootRoute` kendiliğinden
 * [Route.Pair]'e geçer -- ölü token'la sonsuza dek yeniden denemek yerine dürüst ekran.
 */
@Composable
fun WearRoot(tokenStore: TokenStore, api: JarvisApi) {
    val hasTokenState by tokenStore.hasToken.collectAsState(initial = null)
    MaterialTheme {
        when (rootRoute(hasTokenState)) {
            Route.Unknown -> Box(modifier = Modifier.fillMaxSize())
            Route.Pair -> PairScreen()
            Route.Chat -> {
                // Oturum kimliği telefonun oturum düzeniyle aynı aile (spec §5); sunucu
                // sanitize eder. `remember(api)`: WearApp tekil api örneği süresince aynı
                // ViewModel -- ekran yeniden komposed olsa da sohbet durumu (busy/son
                // soru-cevap) hayatta kalır.
                val viewModel = remember(api) {
                    ChatViewModel(chat = api::chat, sessionId = "wear-" + LocalDate.now())
                }
                val chatState by viewModel.state.collectAsState()
                LaunchedEffect(chatState.needsPairing) {
                    if (chatState.needsPairing) tokenStore.clear()
                }
                ChatScreen(viewModel)
            }
        }
    }
}
