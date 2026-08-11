package com.jarvis.data.voice.session

import org.junit.Assert.assertEquals
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
 *
 * Also pins [sttErrorAction], the superset decision that additionally knows whether the
 * cycle that just failed was served by the on-device recognizer: ERROR_LANGUAGE_UNAVAILABLE
 * (13) is the measured failure (tr-TR SODA pack absent) that must fall back to the network
 * recognizer instead of ending the call -- but only while on-device is what just failed;
 * the same code from the network recognizer means there is nowhere left to fall back to,
 * so it is fatal there. The recoverable set must behave identically regardless of which
 * recognizer is in use.
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

    @Test
    fun languageUnavailable_onDevice_fallsBackToNetwork() {
        assertEquals(
            SttErrorAction.FallbackToNetwork,
            sttErrorAction(errorCode = 13, usingOnDeviceRecognizer = true), // ERROR_LANGUAGE_UNAVAILABLE
        )
    }

    @Test
    fun languageUnavailable_onNetwork_isFatal() {
        // Already on the network recognizer -- nowhere left to fall back to.
        assertEquals(
            SttErrorAction.Fatal,
            sttErrorAction(errorCode = 13, usingOnDeviceRecognizer = false), // ERROR_LANGUAGE_UNAVAILABLE
        )
    }

    @Test
    fun recoverableCodes_areRetryRegardlessOfRecognizer() {
        for (code in listOf(6, 7, 8)) { // ERROR_SPEECH_TIMEOUT / ERROR_NO_MATCH / ERROR_RECOGNIZER_BUSY
            assertEquals(SttErrorAction.Retry, sttErrorAction(code, usingOnDeviceRecognizer = true))
            assertEquals(SttErrorAction.Retry, sttErrorAction(code, usingOnDeviceRecognizer = false))
        }
    }

    @Test
    fun representativeFatalCode_isFatalRegardlessOfRecognizer() {
        // ERROR_CLIENT -- not language-related, so the on-device flag must not change it.
        assertEquals(SttErrorAction.Fatal, sttErrorAction(5, usingOnDeviceRecognizer = true))
        assertEquals(SttErrorAction.Fatal, sttErrorAction(5, usingOnDeviceRecognizer = false))
    }
}
