package com.jarvis.ui.voice

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.net.EnrollRequest
import com.jarvis.data.net.NETWORK_MESSAGE
import com.jarvis.data.net.VoiceApi
import java.io.IOException
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import retrofit2.HttpException

/**
 * The "Bu cihazı tanıt" enrollment flow (spec §6 / Task 9): a new device's microphone is
 * a different acoustic channel than the gallery was built on, and the channel-adaptive
 * gallery cannot learn a new one on its own (the adapt gate scores against immutable
 * anchors — that is what stops a poisoning ratchet). Enrollment is the only path onto a
 * new channel, and it needs a fresh liveness proof: the server speaks a 4-digit code over
 * an OPEN voice bridge ([VoiceApi.challenge]), the user repeats it, and only THEN does
 * [VoiceApi.enroll] accept clips (409 otherwise).
 *
 * Each state carries the Turkish label the screen renders for it -- see [label]. Idle and
 * Failed render nothing here: Idle has nothing to report yet, and [EnrollState.Failed]'s
 * own [EnrollState.Failed.message] IS what is shown.
 */
sealed interface EnrollState {
    data object Idle : EnrollState
    data object RequestingCode : EnrollState
    data object WaitingForSpokenCode : EnrollState
    data object Recording : EnrollState
    data object Uploading : EnrollState
    data class Done(val anchors: Int) : EnrollState
    data class Failed(val message: String) : EnrollState
}

/** The Turkish label the screen shows above the enroll button for each phase. */
fun EnrollState.label(): String? = when (this) {
    EnrollState.Idle -> null
    EnrollState.RequestingCode -> "Kod isteniyor…"
    EnrollState.WaitingForSpokenCode -> "Jarvis'in söylediği kodu tekrar et"
    EnrollState.Recording -> "Konuş — örnek alınıyor (3/3)"
    EnrollState.Uploading -> "Kaydediliyor…"
    is EnrollState.Done -> "Bu cihaz tanıtıldı ($anchors örnek)"
    is EnrollState.Failed -> message
}

/**
 * Records raw PCM16 mono clips for enrollment. A narrow interface so
 * [EnrollDeviceViewModel] stays JVM-testable against a fake -- the same split
 * [com.jarvis.data.voice.session.MicSource] uses for [com.jarvis.data.voice.session.AndroidMicSource].
 * The real implementation is [com.jarvis.data.voice.session.AndroidClipRecorder].
 */
interface ClipRecorder {
    /** Records [count] clips of [seconds] each, back to back. */
    suspend fun record(count: Int, seconds: Double): List<ByteArray>
}

private const val CLIP_COUNT = 3
private const val CLIP_SECONDS = 2.0
private const val CODE_NOT_SPOKEN_MESSAGE =
    "Kodu duyabilmen için önce sesli aramayı başlat, sonra tekrar dene."

class EnrollDeviceViewModel(
    private val api: VoiceApi,
    private val recorder: ClipRecorder,
    private val deviceHint: String,
) : ViewModel() {

    private val _state = MutableStateFlow<EnrollState>(EnrollState.Idle)
    val state: StateFlow<EnrollState> = _state.asStateFlow()

    /**
     * Ignored while a request is already in flight (RequestingCode through Uploading) --
     * a double tap on "Bu cihazı tanıt" must not fire a second [VoiceApi.challenge].
     * Callable again from [EnrollState.Idle], [EnrollState.Done] or [EnrollState.Failed]
     * (retry after a failure, or enrolling a further batch of samples).
     */
    fun start() {
        when (_state.value) {
            EnrollState.RequestingCode, EnrollState.WaitingForSpokenCode,
            EnrollState.Recording, EnrollState.Uploading,
            -> return
            else -> Unit
        }
        _state.value = EnrollState.RequestingCode
        viewModelScope.launch {
            try {
                val challenge = api.challenge()
                _state.value = if (challenge.code_spoken) {
                    EnrollState.WaitingForSpokenCode
                } else {
                    EnrollState.Failed(CODE_NOT_SPOKEN_MESSAGE)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpException) {
                _state.value = EnrollState.Failed(mapEnrollError(e))
            } catch (e: IOException) {
                _state.value = EnrollState.Failed(NETWORK_MESSAGE)
            }
        }
    }

    /**
     * The user says they repeated the spoken code over the live bridge. The grant is
     * minted server-side (Task 3's CM gate) and this ViewModel has no way to observe
     * that directly, so this is a plain user-driven transition -- not a fixed wait.
     * [VoiceApi.enroll]'s 409 already covers the case where the grant was not actually
     * minted (code mis-heard, bridge dropped, CM rejected the utterance).
     */
    fun proceedToRecording() {
        if (_state.value != EnrollState.WaitingForSpokenCode) return
        _state.value = EnrollState.Recording
        viewModelScope.launch {
            try {
                val clips = recorder.record(CLIP_COUNT, CLIP_SECONDS)
                _state.value = EnrollState.Uploading
                // android.util.Base64 is not JVM-testable; java.util.Base64 is (and is
                // available from minSdk 26 without desugaring).
                val encoded = clips.map { Base64.getEncoder().encodeToString(it) }
                val response = api.enroll(EnrollRequest(clips = encoded, device_hint = deviceHint))
                _state.value = EnrollState.Done(response.anchors)
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpException) {
                _state.value = EnrollState.Failed(mapEnrollError(e))
            } catch (e: IOException) {
                _state.value = EnrollState.Failed(NETWORK_MESSAGE)
            } catch (e: IllegalStateException) {
                // AndroidClipRecorder's own failure shape (mic busy/unavailable), same
                // convention as AndroidMicSource.start().
                _state.value = EnrollState.Failed(
                    "Kayıt yapılamadı: ${e.message ?: "mikrofon kullanılamıyor"}.",
                )
            }
        }
    }

    private fun mapEnrollError(e: HttpException): String = when (e.code()) {
        409 -> "Kod doğrulanmadı. Jarvis'in söylediği dört haneli kodu tekrar et, sonra yeniden dene."
        422 -> "Alınan ses örneği sahte olarak işaretlendi, kayıt yapılmadı."
        503 -> "Ses doğrulaması şu anda yapılamıyor, birazdan tekrar dene."
        else -> "Kayıt tamamlanamadı (${e.code()})."
    }
}
