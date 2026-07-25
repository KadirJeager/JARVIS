package com.jarvis.ui.voicecall

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.voice.session.VoiceSession
import com.jarvis.data.voice.session.VoiceUiState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.StateFlow

/**
 * Lifecycle adapter over [VoiceSession]. Takes a factory, not a session: the session's
 * mic-pump coroutine must run in [viewModelScope], which does not exist until this
 * object does. The one guarantee this class OWNS (and VoiceCallViewModelTest pins) is
 * that clearing the ViewModel ends a live call — a navigated-away screen can never leak
 * an open microphone or socket.
 */
class VoiceCallViewModel(
    sessionFactory: (CoroutineScope) -> VoiceSession,
) : ViewModel() {

    private val session = sessionFactory(viewModelScope)

    val state: StateFlow<VoiceUiState> = session.state

    fun start() = session.start()

    fun stop() = session.stop()

    /** The Activity reports a denied RECORD_AUDIO here; the session shows it as ERROR. */
    fun onMicPermissionDenied() =
        session.reportError("Mikrofon izni gerekli. Ayarlar > Uygulamalar > Jarvis'ten izin ver.")

    override fun onCleared() = session.stop()
}
