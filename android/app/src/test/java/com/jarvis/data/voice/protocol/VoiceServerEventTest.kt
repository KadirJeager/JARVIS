package com.jarvis.data.voice.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins [parseVoiceServerEvent] against the frozen wire contract in
 * brain/app/voice_protocol.py's `evt_*` helpers. The server is authoritative and this
 * client must never change it -- these tests exist so a future server addition (a new
 * `type`) or a malformed frame degrades gracefully instead of crashing the voice session.
 */
class VoiceServerEventTest {

    @Test
    fun parsesTranscriptEvent() {
        val raw = """{"type":"transcript","role":"user","text":"merhaba"}"""
        val evt = parseVoiceServerEvent(raw)
        assertEquals(VoiceServerEvent.Transcript(role = "user", text = "merhaba"), evt)
    }

    @Test
    fun parsesTranscriptEvent_modelRole() {
        val raw = """{"type":"transcript","role":"model","text":"nasil yardimci olabilirim"}"""
        val evt = parseVoiceServerEvent(raw)
        assertEquals(VoiceServerEvent.Transcript(role = "model", text = "nasil yardimci olabilirim"), evt)
    }

    @Test
    fun parsesTurnCompleteEvent() {
        val raw = """{"type":"turn_complete"}"""
        assertEquals(VoiceServerEvent.TurnComplete, parseVoiceServerEvent(raw))
    }

    @Test
    fun parsesErrorEvent() {
        val raw = """{"type":"error","message":"model kullanilamiyor"}"""
        assertEquals(VoiceServerEvent.Error("model kullanilamiyor"), parseVoiceServerEvent(raw))
    }

    @Test
    fun parsesSpeakerEvent() {
        val raw = """{"type":"speaker","role":"user","verified":true,"score":0.71}"""
        val evt = parseVoiceServerEvent(raw)
        assertEquals(VoiceServerEvent.Speaker(role = "user", verified = true, score = 0.71), evt)
    }

    @Test
    fun parsesSpeakerEvent_unverified() {
        val raw = """{"type":"speaker","role":"user","verified":false,"score":0.12}"""
        val evt = parseVoiceServerEvent(raw)
        assertEquals(VoiceServerEvent.Speaker(role = "user", verified = false, score = 0.12), evt)
    }

    /**
     * The docstring in voice_protocol.py says the server may add events later -- an
     * unrecognized `type` must be silently ignored, not crash the session.
     */
    @Test
    fun unknownEventType_isIgnored_notCrash() {
        val raw = """{"type":"future_event","payload":{"whatever":1}}"""
        assertNull(parseVoiceServerEvent(raw))
    }

    @Test
    fun malformedJson_isIgnored_notCrash() {
        assertNull(parseVoiceServerEvent("not json at all {{{"))
    }

    @Test
    fun jsonArray_isIgnored_notCrash() {
        assertNull(parseVoiceServerEvent("""[1,2,3]"""))
    }

    @Test
    fun missingTypeField_isIgnored() {
        assertNull(parseVoiceServerEvent("""{"role":"user","text":"no type field"}"""))
    }

    @Test
    fun transcriptMissingRequiredField_isIgnored() {
        // Missing "text" -- must not throw, must be treated as unusable.
        assertNull(parseVoiceServerEvent("""{"type":"transcript","role":"user"}"""))
    }

    @Test
    fun errorMissingMessage_isIgnored() {
        assertNull(parseVoiceServerEvent("""{"type":"error"}"""))
    }

    @Test
    fun speakerMissingScore_isIgnored() {
        assertNull(parseVoiceServerEvent("""{"type":"speaker","role":"user","verified":true}"""))
    }

    /** Extra unknown fields alongside a known type must not break parsing (forward compat). */
    @Test
    fun knownEventWithExtraUnknownFields_stillParses() {
        val raw = """{"type":"transcript","role":"user","text":"merhaba","future_field":"x"}"""
        val evt = parseVoiceServerEvent(raw)
        assertEquals(VoiceServerEvent.Transcript("user", "merhaba"), evt)
    }

    @Test
    fun rateConstants_matchTheFrozenProtocol() {
        // brain/app/voice_protocol.py: AUDIO_IN_RATE = 16000, AUDIO_OUT_RATE = 24000.
        // Mic capture goes UP at 16k; model playback comes DOWN at 24k -- swapping these
        // is the classic chipmunk/slow-motion bug this test exists to catch.
        assertEquals(16000, AUDIO_IN_RATE_HZ)
        assertEquals(24000, AUDIO_OUT_RATE_HZ)
        assertTrue(AUDIO_IN_RATE_HZ != AUDIO_OUT_RATE_HZ)
    }
}
