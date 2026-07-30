package com.jarvis.data.voice.protocol

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pins the protocol-v2 client->server TEXT frames ([buildSpeechStartFrame],
 * [buildUserTextFrame]) against the frozen wire contract: the server's voice pipeline
 * keys on the exact `type` values and on `utterance_final` -- a renamed field here is a
 * silent protocol break with no client-visible error.
 */
class VoiceClientFramesTest {

    @Test
    fun speechStart_hasExactWireShape() {
        val obj = Json.parseToJsonElement(buildSpeechStartFrame()).jsonObject
        assertEquals("speech_start", obj.getValue("type").jsonPrimitive.content)
        assertEquals(setOf("type"), obj.keys)
    }

    @Test
    fun userText_hasExactWireShape() {
        val obj = Json.parseToJsonElement(buildUserTextFrame("merhaba jarvis")).jsonObject
        assertEquals("user_text", obj.getValue("type").jsonPrimitive.content)
        assertEquals("merhaba jarvis", obj.getValue("text").jsonPrimitive.content)
        assertEquals(true, obj.getValue("utterance_final").jsonPrimitive.content.toBoolean())
        assertEquals(setOf("type", "text", "utterance_final"), obj.keys)
    }

    /** Recognizer output is free-form user speech -- quotes, newlines and Turkish
     *  characters must survive the round trip byte-identical. */
    @Test
    fun userText_escapesArbitraryRecognizerOutput() {
        val tricky = "\"Tırnak\" içinde\nyeni satır — ğüşiıöç"
        val obj = Json.parseToJsonElement(buildUserTextFrame(tricky)).jsonObject
        assertEquals(tricky, obj.getValue("text").jsonPrimitive.content)
    }
}
