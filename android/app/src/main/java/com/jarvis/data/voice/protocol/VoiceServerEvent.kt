package com.jarvis.data.voice.protocol

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.jsonPrimitive

/**
 * Client's mirror of brain/app/voice_protocol.py's `evt_*` helpers -- this file has NO
 * Android imports on purpose (see VoiceSession's package doc) so the wire parsing stays
 * plain-JVM testable. The server is the permanent, frozen contract; this is a reader over
 * it, never the other way around.
 */
sealed interface VoiceServerEvent {
    data class Transcript(val role: String, val text: String) : VoiceServerEvent

    /** Protocol v2: the server's reply TEXT. The client voices it with the on-device TTS
     *  instead of receiving 24kHz model PCM. One reply may arrive as several events. */
    data class JarvisText(val text: String) : VoiceServerEvent
    data object TurnComplete : VoiceServerEvent
    data class Error(val message: String) : VoiceServerEvent
    data class Speaker(val role: String, val verified: Boolean, val score: Double) : VoiceServerEvent
}

/**
 * One accumulated line of the live transcript, as shown in the UI. Deliberately a
 * separate type from [VoiceServerEvent.Transcript] (same shape today): that one is a
 * single parsed wire event, this one is what [com.jarvis.data.voice.session.VoiceSession]
 * appends into its running [com.jarvis.data.voice.session.VoiceUiState.transcript] list --
 * the same event/UI-model split as `HistoryMessage` vs `UiMessage` in data.chat.
 */
data class TranscriptLine(val role: String, val text: String)

/** Mic capture (client -> server) rate in Hz. Mirrors brain/app/voice_protocol.py's
 *  AUDIO_IN_RATE. There is no AUDIO_OUT rate anymore: protocol v2 keeps the mic PCM
 *  stream only for server-side speaker-ID, and replies arrive as text (jarvis_text)
 *  voiced by the on-device TTS. */
const val AUDIO_IN_RATE_HZ = 16000

private val json = Json { ignoreUnknownKeys = true }

/**
 * Parses one server TEXT frame into a [VoiceServerEvent]. Returns null for anything the
 * client cannot use: malformed JSON, a non-object frame, an unrecognized `type` (the
 * server's docstring says it may add events later -- those must be ignored, not crash the
 * session), or a known `type` missing a field it needs.
 */
fun parseVoiceServerEvent(raw: String): VoiceServerEvent? {
    val obj = runCatching { json.parseToJsonElement(raw) }.getOrNull() as? JsonObject ?: return null
    return when (obj["type"]?.jsonPrimitive?.contentOrNull) {
        "transcript" -> {
            val role = obj["role"]?.jsonPrimitive?.contentOrNull ?: return null
            val text = obj["text"]?.jsonPrimitive?.contentOrNull ?: return null
            VoiceServerEvent.Transcript(role, text)
        }
        "jarvis_text" -> {
            val text = obj["text"]?.jsonPrimitive?.contentOrNull ?: return null
            VoiceServerEvent.JarvisText(text)
        }
        "turn_complete" -> VoiceServerEvent.TurnComplete
        "error" -> {
            val message = obj["message"]?.jsonPrimitive?.contentOrNull ?: return null
            VoiceServerEvent.Error(message)
        }
        "speaker" -> {
            val role = obj["role"]?.jsonPrimitive?.contentOrNull ?: return null
            val verified = obj["verified"]?.jsonPrimitive?.booleanOrNull ?: return null
            val score = obj["score"]?.jsonPrimitive?.doubleOrNull ?: return null
            VoiceServerEvent.Speaker(role, verified, score)
        }
        else -> null
    }
}
