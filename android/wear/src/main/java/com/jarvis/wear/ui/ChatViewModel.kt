package com.jarvis.wear.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.wear.data.JarvisApiException
import com.jarvis.wear.data.UnauthorizedException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

data class ChatState(
    val busy: Boolean = false,
    val lastQuestion: String? = null,
    val lastReply: String? = null,
    val error: String? = null,
    val needsPairing: Boolean = false,
)

/** Saat ekranında 3-4 hazır komut: tek dokunuş, aynı chat ucu (spec §5). */
val QUICK_COMMANDS = listOf(
    "Durum raporu ver",
    "Hatırlatmalarımı listele",
    "Bekleyen onaylarım var mı?",
    "Bugün ne yapmalıyım?",
)

class ChatViewModel(
    private val chat: suspend (sessionId: String, message: String) -> String,
    private val sessionId: String,
) : ViewModel() {
    private val _state = MutableStateFlow(ChatState())
    val state: StateFlow<ChatState> = _state

    /** Ağdan geçmeyen, cihaz-yerel bir hatayı aynı hata yüzeyine yazar (Task 6: örn.
     * konuşma tanıma etkinliği yok / `ActivityNotFoundException`). Kaynağına göre ayrı
     * bir metin kutusu açmak yerine [ChatState.error] tek yüzey kalır -- ekranda hangi
     * hatayı gösterdiği kullanıcı için önemli değil, GÖRDÜĞÜ önemli. */
    fun reportInputError(message: String) {
        _state.value = _state.value.copy(error = message)
    }

    fun send(text: String) {
        val message = text.trim()
        if (message.isEmpty() || _state.value.busy) return
        _state.value = _state.value.copy(busy = true, lastQuestion = message, error = null)
        viewModelScope.launch {
            try {
                val reply = chat(sessionId, message)
                _state.value = _state.value.copy(busy = false, lastReply = reply)
            } catch (e: UnauthorizedException) {
                _state.value = _state.value.copy(busy = false, needsPairing = true)
            } catch (e: JarvisApiException) {
                _state.value = _state.value.copy(busy = false, error = e.userMessage)
            } catch (e: CancellationException) {
                // Yapısal eşzamanlılık (JarvisApi.chat ile aynı sözleşme, Task 5 review fix):
                // ekran/ViewModel kapanıp bu coroutine iptal edildiğinde bu bir uygulama
                // hatası DEĞİL -- olduğu gibi yukarı yükselmeli, yoksa "kullanıcı ekrandan
                // ayrıldı" durumu sahte bir "Beklenmeyen hata" mesajına dönüşür.
                throw e
            } catch (e: Exception) {
                _state.value = _state.value.copy(busy = false, error = "Beklenmeyen hata; tekrar dene.")
            }
        }
    }
}
