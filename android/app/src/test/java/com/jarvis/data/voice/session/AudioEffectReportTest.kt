package com.jarvis.data.voice.session

import java.util.UUID
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AudioEffectReportTest {
    // Arbitrary UUIDs, not android.media.audiofx.AudioEffect.EFFECT_TYPE_AEC: the pure
    // function takes the AEC type as a parameter precisely so this JVM test never has to
    // touch the platform class (whose static initializer would hit the unmocked-android
    // wall outside Robolectric/instrumented tests).
    private val aecType: UUID = UUID.fromString("7b491460-8d4d-11e0-bd61-0002a5d5c51b")
    private val otherType: UUID = UUID.fromString("0f8d0d66-3b16-454d-a184-a0e05c98d24a")

    @Test
    fun reportNamesEffectsAndSilenceState() {
        val line = formatEffectReport(
            listOf(MicEffect("AEC", aecType), MicEffect("NS", otherType)),
            silenced = false,
            sdkInt = 34,
            aecType = aecType,
        )
        assertTrue(line.contains("effects=AEC,NS"))
        assertTrue(line.contains("silenced=false"))
        assertTrue(line.contains("aec=true"))
    }

    @Test
    fun reportFlagsMissingAec() {
        val line = formatEffectReport(
            listOf(MicEffect("NS", otherType)),
            silenced = false,
            sdkInt = 34,
            aecType = aecType,
        )
        assertTrue(line.contains("aec=false"))
    }

    @Test
    fun reportDetectsAecByTypeNotName() {
        // Pins the review defect: a vendor descriptor named "Acoustic Echo Canceler"
        // contains no contiguous "aec" substring in any casing, so name-substring
        // matching would silently report aec=false for a device that genuinely has AEC
        // attached -- inverting the exact measurement this telemetry exists to produce.
        // AEC presence must come from the standardized type UUID, never from the name.
        val line = formatEffectReport(
            listOf(MicEffect("Acoustic Echo Canceler", aecType)),
            silenced = false,
            sdkInt = 34,
            aecType = aecType,
        )
        assertTrue(line.contains("aec=true"))
    }

    @Test
    fun reportIsHonestWhenTheApiIsUnavailable() {
        // getEffects()/isClientSilenced() are API 29+; minSdk is 26, so below that
        // the answer is "unknown", never a cheerful default.
        val line = formatEffectReport(emptyList(), silenced = false, sdkInt = 28, aecType = aecType)
        assertTrue(line.contains("effects=unknown"))
        assertFalse(line.contains("aec=true"))
    }
}
