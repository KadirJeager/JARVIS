package com.jarvis.wear.ui

import org.junit.Assert.assertEquals
import org.junit.Test

/** TTS sarmalayıcısının sözleşmesi: yeni cevap okunur, aynı cevap tekrar
 * OKUNMAZ (state yeniden compose olduğunda kekeleme olmasın), null susar. */
class VoiceReplySpeakerTest {
    @Test fun `speaks each new reply once`() {
        val spoken = mutableListOf<String>()
        val speaker = ReplySpeaker { spoken += it }
        speaker.speakIfNew("merhaba")
        speaker.speakIfNew("merhaba")
        speaker.speakIfNew("ikinci")
        assertEquals(listOf("merhaba", "ikinci"), spoken)
    }

    @Test fun `null is silent`() {
        val spoken = mutableListOf<String>()
        ReplySpeaker { spoken += it }.speakIfNew(null)
        assertEquals(emptyList<String>(), spoken)
    }
}
