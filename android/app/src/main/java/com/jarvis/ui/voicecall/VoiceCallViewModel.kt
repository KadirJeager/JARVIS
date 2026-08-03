package com.jarvis.ui.voicecall

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.voice.session.VoiceSession
import com.jarvis.data.voice.session.VoiceUiState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

/**
 * Lifecycle adapter over [VoiceSession]. Takes a factory, not a session: the session's
 * mic-pump coroutine must run in [viewModelScope], which does not exist until this
 * object does. The one guarantee this class OWNS (and VoiceCallViewModelTest pins) is
 * that clearing the ViewModel ends a live call — a navigated-away screen can never leak
 * an open microphone or socket.
 */
class VoiceCallViewModel(
    sessionFactory: (CoroutineScope) -> VoiceSession,
    private val refreshAuth: suspend () -> Unit = {},
) : ViewModel() {

    private val session = sessionFactory(viewModelScope)

    val state: StateFlow<VoiceUiState> = session.state

    fun start() {
        viewModelScope.launch {
            // The hello frame has no 401-refresh-retry path like the HTTP stack does:
            // a cached token can be an hour old and dead. Mint a fresh one BEFORE
            // dialing; if refresh fails, start() still runs and the session surfaces
            // its own token/handshake error to the overlay.
            runCatching { refreshAuth() }
            session.start()
        }
    }

    fun stop() = session.stop()

    /** Deterministic barge-in from the UI: cut Jarvis off and take the floor.
     *  Replaces voice barge-in, which the echo guard had to give up. */
    fun interrupt() = session.interrupt()

    /** The Activity reports a denied RECORD_AUDIO here; the session shows it as ERROR. */
    fun onMicPermissionDenied() =
        session.reportError("Mikrofon izni gerekli. Ayarlar > Uygulamalar > Jarvis'ten izin ver.")

    override fun onCleared() = session.stop()
}
