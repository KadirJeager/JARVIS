package com.jarvis.wear

import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.util.Log
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.lifecycle.viewModelScope
import androidx.wear.compose.material.MaterialTheme
import com.jarvis.wear.data.JarvisApi
import com.jarvis.wear.data.TokenStore
import com.jarvis.wear.ui.ChatScreen
import com.jarvis.wear.ui.ChatViewModel
import com.jarvis.wear.ui.PairScreen
import com.jarvis.wear.ui.ReplySpeaker
import java.time.LocalDate
import java.util.Locale
import kotlinx.coroutines.cancel

/**
 * TTS ömrü (Task 6): motor burada, Activity seviyesinde, TEK sefer kurulur ve
 * [onDestroy]'da kapatılır -- [ChatScreen] her recomposition'da yeniden `TextToSpeech(...)`
 * açıp kapatmaz (pahalı + [ReplySpeaker]'ın "aynı cevabı iki kez okuma" sözleşmesini
 * anlamsızlaştırır: motor sıfırdan kurulursa `last` hafızası da sıfırlanır).
 *
 * Dürüst düşüş: cihazda/emülatör imajında `tr-TR` ses verisi yoksa bu bir uygulama HATASI
 * değil -- [TextToSpeech.setLanguage] `LANG_MISSING_DATA`/`LANG_NOT_SUPPORTED` döner, bir
 * log satırı yazılır VE (görev talimatının kritik kısıtı: sessizlik asla tek başına yeterli
 * açıklama değildir) kullanıcı TEK seferlik bir Türkçe Toast görür -- cevap metni zaten
 * [ChatScreen]'in kartında görünür durumda, o yüzden bu bir "ölü buton" değil, dürüst bir
 * metin-moduna düşüş.
 */
class MainActivity : ComponentActivity() {
    private lateinit var tts: TextToSpeech

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val app = application as WearApp

        tts = TextToSpeech(this) { status ->
            if (status != TextToSpeech.SUCCESS) {
                Log.w(LOG_TAG, "TTS motoru kurulamadı: $status")
                return@TextToSpeech
            }
            val langResult = tts.setLanguage(Locale("tr", "TR"))
            if (langResult == TextToSpeech.LANG_MISSING_DATA ||
                langResult == TextToSpeech.LANG_NOT_SUPPORTED
            ) {
                Log.w(LOG_TAG, "tr-TR TTS verisi yok ($langResult); sesli yanıt atlanacak")
                Toast.makeText(
                    this,
                    "Bu cihazda Türkçe sesli okuma yok; yanıtlar yalnızca yazı olarak gösterilecek.",
                    Toast.LENGTH_LONG,
                ).show()
            }
        }
        val speaker = ReplySpeaker { text -> tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "jarvis") }

        setContent { WearRoot(app.tokenStore, app.api, speaker) }
    }

    override fun onDestroy() {
        tts.shutdown()
        super.onDestroy()
    }

    private companion object {
        const val LOG_TAG = "MainActivity"
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
 *
 * Task 5 review fix #2 (Finding 1): [ChatViewModel] burada `remember` ile YARATILIR ama
 * [androidx.lifecycle.ViewModelStore]/`viewModel()` ile YÖNETİLMEZ -- bilinçli tercih:
 * `viewModel()`'e geçmek, [Route.Chat] koluna yeniden girildiğinde (eşleştirme sonrası)
 * AYNI eski örneği geri getirir -- üstünde hâlâ `needsPairing = true` duran bir örnek --
 * ve efekt YENİ token'ı anında bir daha temizler (sonsuz döngü). `remember(api)` composition
 * pozisyonuna bağlı: [Route.Chat] koludan ayrılıp (Pair'e düşünce) yeniden girildiğinde bu
 * `when` dalı sıfırdan komposed edilir, dolayısıyla HER giriş taze `ChatState()`'li taze bir
 * örnek alır -- döngü riski yok. Bunun bedeli: hiçbir çerçeve `onCleared()` çağırmaz, o yüzden
 * [androidx.compose.runtime.DisposableEffect] ile `viewModelScope`'u elle iptal ediyoruz --
 * aksi halde ekran Pair'e düşse bile devam eden bir `chat()` çağrısının coroutine'i sızar.
 */
@Composable
fun WearRoot(tokenStore: TokenStore, api: JarvisApi, speaker: ReplySpeaker) {
    val hasTokenState by tokenStore.hasToken.collectAsState(initial = null)
    MaterialTheme {
        when (rootRoute(hasTokenState)) {
            Route.Unknown -> Box(modifier = Modifier.fillMaxSize())
            Route.Pair -> PairScreen()
            Route.Chat -> {
                // Oturum kimliği telefonun oturum düzeniyle aynı aile (spec §5); sunucu
                // sanitize eder. `remember(api)`: bu `when` dalı komposed kaldığı sürece aynı
                // ViewModel -- ekran yeniden komposed olsa da sohbet durumu (busy/son
                // soru-cevap) hayatta kalır. Dalın kendisi terk edilip yeniden girildiğinde
                // (örn. needsPairing -> Pair -> yeniden eşleştir -> Chat) taze bir örnek
                // yaratılır; bkz. fonksiyon KDoc'u.
                val viewModel = remember(api) {
                    ChatViewModel(chat = api::chat, sessionId = "wear-" + LocalDate.now())
                }
                DisposableEffect(viewModel) {
                    onDispose { viewModel.viewModelScope.cancel() }
                }
                val chatState by viewModel.state.collectAsState()
                LaunchedEffect(chatState.needsPairing) {
                    if (chatState.needsPairing) tokenStore.clear()
                }
                ChatScreen(viewModel, speaker)
            }
        }
    }
}
