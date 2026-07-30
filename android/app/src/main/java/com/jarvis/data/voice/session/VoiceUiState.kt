package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.TranscriptLine

/**
 * Where a live voice call stands. Five states, not a boolean, because the UI needs to
 * distinguish "socket handshaking" from "mic live" from "assistant is talking" -- the
 * same reasoning as [com.jarvis.ui.chat.AuthPhase] one level up.
 */
enum class VoicePhase {
    /** No call in progress. */
    IDLE,

    /** Socket connecting / hello not yet acknowledged. */
    CONNECTING,

    /** Duplex is live: mic is streaming (speaker-ID), the on-device recognizer is
     *  armed, and the on-device TTS is not talking right now. */
    LISTENING,

    /** The on-device TTS is currently voicing a `jarvis_text` reply. */
    SPEAKING,

    /** Something ended the call: a server error, a transport failure, a fatal
     *  recognizer error, or (before any socket exists) a denied microphone permission.
     *  See [VoiceUiState.errorMessage]. */
    ERROR,
}

/** Immutable UI state for the live voice call, mirrored from [VoiceSession]. */
data class VoiceUiState(
    val phase: VoicePhase = VoicePhase.IDLE,
    val transcript: List<TranscriptLine> = emptyList(),
    /** The on-device recognizer's interim hypothesis for the utterance in progress.
     *  Shown as a dimmed user bubble; replaced by the final `user_text` line (or
     *  cleared) when the utterance ends. Null when nothing is being heard. */
    val partialText: String? = null,
    val errorMessage: String? = null,
    /** Last `speaker` event's verdict, if any arrived this call. Not surfaced as its own
     *  UI element yet (spec §11 risk-fusion is server-owned) -- kept so a future badge
     *  does not need a protocol change to add. */
    val lastSpeakerVerified: Boolean? = null,
)
