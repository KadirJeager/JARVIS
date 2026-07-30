package com.jarvis.data.voice.protocol

import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json

/**
 * Client -> server TEXT frames of protocol v2 (after the hello). These are the frames the
 * on-device STT pipeline produces instead of the old Gemini-Live audio turn-taking:
 *
 *  - `speech_start`: fired once per utterance, when SpeechRecognizer reports the user
 *    started talking. The server uses it for barge-in bookkeeping on its side.
 *  - `user_text`: the FINAL transcription of one utterance, sent when the recognizer
 *    delivers onResults. This replaces the model-side transcription entirely.
 *
 * Wire-shape rules are the same as [buildVoiceHello]: property names ARE the JSON keys.
 */
@Serializable
private data class SpeechStartWire(
    val type: String,
)

@Serializable
private data class UserTextWire(
    val type: String,
    val text: String,
    val utterance_final: Boolean,
)

/** `{"type":"speech_start"}` -- one frame per utterance, at speech onset. */
fun buildSpeechStartFrame(): String = Json.encodeToString(SpeechStartWire(type = "speech_start"))

/** `{"type":"user_text","text":...,"utterance_final":true}` -- one frame per final STT result. */
fun buildUserTextFrame(text: String): String =
    Json.encodeToString(UserTextWire(type = "user_text", text = text, utterance_final = true))
