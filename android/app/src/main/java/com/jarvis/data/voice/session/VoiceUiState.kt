package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.TranscriptLine

/**
 * Where a live voice call stands. Five states, not a boolean, because the UI needs to
 * distinguish "socket handshaking" from "mic live" from "model is talking back" -- the
 * same reasoning as [com.jarvis.ui.chat.AuthPhase] one level up.
 */
enum class VoicePhase {
    /** No call in progress. */
    IDLE,

    /** Socket connecting / hello not yet acknowledged. */
    CONNECTING,

    /** Duplex is live: mic is streaming, no model audio arriving right now. */
    LISTENING,

    /** Model audio is currently arriving and being played back. */
    SPEAKING,

    /** Something ended the call: a server error, a transport failure, or (before any
     *  socket exists) a denied microphone permission. See [VoiceUiState.errorMessage]. */
    ERROR,
}

/** Immutable UI state for the live voice call, mirrored from [VoiceSession]. */
data class VoiceUiState(
    val phase: VoicePhase = VoicePhase.IDLE,
    val transcript: List<TranscriptLine> = emptyList(),
    val errorMessage: String? = null,
    /** Last `speaker` event's verdict, if any arrived this call. Not surfaced as its own
     *  UI element yet (spec §11 risk-fusion is server-owned) -- kept so a future badge
     *  does not need a protocol change to add. */
    val lastSpeakerVerified: Boolean? = null,
)
