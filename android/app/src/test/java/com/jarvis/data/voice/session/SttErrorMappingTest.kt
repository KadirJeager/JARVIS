package com.jarvis.data.voice.session

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pins [isRecoverableSttError]: which `SpeechRecognizer.onError` codes re-arm listening
 * (the anti-stall guard) and which end the call with a user-visible error. The literals
 * ARE the platform constants (ERROR_SPEECH_TIMEOUT=6, ERROR_NO_MATCH=7,
 * ERROR_RECOGNIZER_BUSY=8) -- numbers on purpose, so a constant mix-up in the mapping is
 * caught here instead of silently flipping a call-fatal error into an infinite restart
 * loop (or vice versa).
 */
class SttErrorMappingTest {

    @Test
    fun silenceOutcomes_areRecoverable() {
        assertTrue(isRecoverableSttError(6)) // ERROR_SPEECH_TIMEOUT
        assertTrue(isRecoverableSttError(7)) // ERROR_NO_MATCH
        assertTrue(isRecoverableSttError(8)) // ERROR_RECOGNIZER_BUSY (transient service race)
    }

    @Test
    fun persistentFailures_areFatal() {
        assertFalse(isRecoverableSttError(1)) // ERROR_NETWORK_TIMEOUT
        assertFalse(isRecoverableSttError(2)) // ERROR_NETWORK
        assertFalse(isRecoverableSttError(3)) // ERROR_AUDIO
        assertFalse(isRecoverableSttError(4)) // ERROR_SERVER
        assertFalse(isRecoverableSttError(5)) // ERROR_CLIENT
        assertFalse(isRecoverableSttError(9)) // ERROR_INSUFFICIENT_PERMISSIONS
        assertFalse(isRecoverableSttError(10)) // ERROR_TOO_MANY_REQUESTS
    }
}
