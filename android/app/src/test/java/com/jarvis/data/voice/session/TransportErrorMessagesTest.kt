package com.jarvis.data.voice.session

import java.io.EOFException
import java.net.ConnectException
import java.net.SocketException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The saha finding behind this: the overlay showed the user raw JVM socket prose —
 * "Software caused connection abort", "EOFException". The user-facing failure text must
 * say what happened in plain Turkish; the exception class is the ONLY reliable signal
 * (messages differ across vendors and Android versions, so mapping on the class, never
 * on the message string).
 */
class TransportErrorMessagesTest {

    @Test
    fun eof_meansServerDroppedTheConnection() {
        assertEquals(
            "Sunucu bağlantıyı beklenmedik şekilde kapattı",
            humanizeTransportError(EOFException("\\n not found: limit=0")),
        )
    }

    @Test
    fun socketException_meansTheNetworkDropped() {
        assertEquals(
            "Ağ bağlantısı koptu",
            humanizeTransportError(SocketException("Software caused connection abort")),
        )
    }

    @Test
    fun connectAndDns_meanServerUnreachable() {
        assertEquals("Sunucuya ulaşılamadı", humanizeTransportError(ConnectException("refused")))
        assertEquals("Sunucuya ulaşılamadı", humanizeTransportError(UnknownHostException("host")))
    }

    @Test
    fun timeout_saysTimeout() {
        assertEquals(
            "Bağlantı zaman aşımına uğradı",
            humanizeTransportError(SocketTimeoutException("timeout")),
        )
    }

    /** Unknown classes must NOT leak raw JVM prose ("Expected HTTP 101...", SSL chains)
     *  to the user — generic Turkish, with the class name for the bug report. */
    @Test
    fun unknownThrowable_getsGenericTurkish_withClassNameForDiagnosis() {
        assertEquals(
            "Bağlantı kurulamadı (IllegalStateException)",
            humanizeTransportError(IllegalStateException("Expected HTTP 101 response but was '403 Forbidden'")),
        )
        assertEquals(
            "Bağlantı kurulamadı (IllegalStateException)",
            humanizeTransportError(IllegalStateException()),
        )
    }
}
