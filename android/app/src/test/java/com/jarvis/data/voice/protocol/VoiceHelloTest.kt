package com.jarvis.data.voice.protocol

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pins [buildVoiceHello] against brain/app/voice_protocol.py's `parse_hello`: the first
 * TEXT frame must be `{"token", "device_hint"?, "presence"?}`. Field NAMES are the wire
 * contract (snake_case, exact) -- a renamed field here silently breaks auth or drops the
 * speaker-trust-fusion signal server-side, with no client-visible error.
 */
class VoiceHelloTest {

    @Test
    fun buildsExactWireShape() {
        val raw = buildVoiceHello(token = "tok-123", deviceHint = "android-phone", presence = "foreground")
        val obj = Json.parseToJsonElement(raw).jsonObject

        assertEquals("tok-123", obj.getValue("token").jsonPrimitive.content)
        assertEquals("android-phone", obj.getValue("device_hint").jsonPrimitive.content)
        assertEquals("foreground", obj.getValue("presence").jsonPrimitive.content)
        assertEquals(setOf("token", "device_hint", "presence"), obj.keys)
    }

    @Test
    fun defaultsPresenceToForeground() {
        val raw = buildVoiceHello(token = "tok-123", deviceHint = "android-phone")
        val obj = Json.parseToJsonElement(raw).jsonObject
        assertEquals("foreground", obj.getValue("presence").jsonPrimitive.content)
    }
}
