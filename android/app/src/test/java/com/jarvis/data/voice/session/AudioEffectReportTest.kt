package com.jarvis.data.voice.session

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioEffectReportTest {
    @Test
    fun reportNamesEffectsAndSilenceState() {
        val line = formatEffectReport(listOf("AEC", "NS"), silenced = false, sdkInt = 34)
        assertTrue(line.contains("effects=AEC,NS"))
        assertTrue(line.contains("silenced=false"))
        assertTrue(line.contains("aec=true"))
    }

    @Test
    fun reportFlagsMissingAec() {
        val line = formatEffectReport(listOf("NS"), silenced = false, sdkInt = 34)
        assertTrue(line.contains("aec=false"))
    }

    @Test
    fun reportIsHonestWhenTheApiIsUnavailable() {
        // getEffects()/isClientSilenced() are API 29+; minSdk is 26, so below that
        // the answer is "unknown", never a cheerful default.
        val line = formatEffectReport(emptyList(), silenced = false, sdkInt = 28)
        assertTrue(line.contains("effects=unknown"))
        assertFalse(line.contains("aec=true"))
    }
}
