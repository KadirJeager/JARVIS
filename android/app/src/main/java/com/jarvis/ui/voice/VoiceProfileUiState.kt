package com.jarvis.ui.voice

import com.jarvis.data.voice.VoiceProfile

/**
 * The biometric gate, modelled the same way AuthPhase models sign-in: the host owns
 * the Android-side prompt and reports back, so the ViewModel stays JVM-testable.
 */
enum class GatePhase {
    /** The prompt is up (or about to be). Show nothing sensitive. */
    CHECKING,

    /** The user proved themselves to the phone. Content may load. */
    UNLOCKED,

    /** Cancelled, failed, or no lock configured. Offer a retry, show no content. */
    DENIED,
}

data class VoiceProfileUiState(
    val gate: GatePhase = GatePhase.CHECKING,
    val loading: Boolean = false,
    val profile: VoiceProfile? = null,
    val summary: QualitySummary? = null,
    val error: String? = null,
    /** Id of the sample/history row whose mutation is in flight; null when idle. */
    val mutatingId: String? = null,
    /** The whole profile was deleted; the host should leave the screen. */
    val deleted: Boolean = false,
)
