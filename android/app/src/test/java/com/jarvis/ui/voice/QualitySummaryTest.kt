package com.jarvis.ui.voice

import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceQuality
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class QualitySummaryTest {

    private fun quality(
        mean: Double? = null,
        fail: Double? = null,
        last10: Double? = null,
        previous10: Double? = null,
        byLabel: Map<String, Double> = emptyMap(),
    ) = VoiceQuality(mean, fail, emptyMap(), byLabel, last10, previous10)

    @Test
    fun noHistoryYet_saysSoPlainly_ratherThanShowingAFakeZero() {
        val s = summarize(quality(), VoiceCounts(7, 0, 0))
        assertEquals(TrendDirection.UNKNOWN, s.trend)
        assertTrue(s.headline.contains("Henüz"))
        assertTrue("sıfır skor uydurulmamalı: ${s.detail}", !s.detail.contains("0.00"))
    }

    @Test
    fun noProfileAtAll_tellsTheUserToEnroll() {
        val s = summarize(quality(), VoiceCounts(0, 0, 0))
        assertTrue(s.headline.contains("Ses kimliği yok"))
    }

    @Test
    fun highMean_readsAsStrongRecognition() {
        val s = summarize(quality(mean = 0.74, fail = 0.05), VoiceCounts(7, 2, 0))
        assertTrue(s.headline.contains("güçlü"))
        assertTrue(s.detail.contains("0.74"))
    }

    @Test
    fun middlingMean_readsAsUsableButImprovable() {
        val s = summarize(quality(mean = 0.48, fail = 0.2), VoiceCounts(7, 1, 0))
        assertTrue(s.headline.contains("orta"))
    }

    @Test
    fun lowMean_readsAsWeak_andMentionsTheFailureRate() {
        val s = summarize(quality(mean = 0.22, fail = 0.6), VoiceCounts(7, 0, 0))
        assertTrue(s.headline.contains("zayıf"))
        assertTrue("başarısız oran yüzde olarak görünmeli: ${s.detail}", s.detail.contains("%60"))
    }

    @Test
    fun risingTrend_isReportedAsUp() {
        assertEquals(
            TrendDirection.UP,
            summarize(quality(mean = 0.6, last10 = 0.70, previous10 = 0.60), VoiceCounts(7, 0, 0)).trend,
        )
    }

    @Test
    fun fallingTrend_isReportedAsDown() {
        assertEquals(
            TrendDirection.DOWN,
            summarize(quality(mean = 0.6, last10 = 0.55, previous10 = 0.68), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** A hair of movement is noise, not a trend — it must not flip the arrow. */
    @Test
    fun tinyMovement_readsAsFlat_notAsATrend() {
        assertEquals(
            TrendDirection.FLAT,
            summarize(quality(mean = 0.6, last10 = 0.701, previous10 = 0.700), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** Only one window of history exists yet — there is nothing to compare against. */
    @Test
    fun missingPreviousWindow_isUnknown_notFlat() {
        assertEquals(
            TrendDirection.UNKNOWN,
            summarize(quality(mean = 0.6, last10 = 0.70, previous10 = null), VoiceCounts(7, 0, 0)).trend,
        )
    }

    /** Label-linked means are what calibration actually uses (spec §6.1). */
    @Test
    fun labelBreakdown_appearsWithTurkishDisplayNames() {
        val s = summarize(
            quality(mean = 0.6, byLabel = mapOf("gurultulu" to 0.41)),
            VoiceCounts(7, 0, 0),
        )
        assertTrue(s.detail.contains("Gürültülü"))
        assertTrue(s.detail.contains("0.41"))
    }

    @Test
    fun labelDisplayNames_coverTheWholeServerVocabulary() {
        assertEquals("Sağlıklı", labelDisplayName("saglikli"))
        assertEquals("Hasta", labelDisplayName("hasta"))
        assertEquals("Yorgun", labelDisplayName("yorgun"))
        assertEquals("Gürültülü", labelDisplayName("gurultulu"))
        assertEquals("Kulaklık", labelDisplayName("kulaklik"))
        assertEquals("Hoparlör", labelDisplayName("hoparlor"))
        assertEquals("Araç", labelDisplayName("arac"))
    }

    /** An unknown label must still render — the server's vocabulary can grow. */
    @Test
    fun unknownLabel_rendersItselfRatherThanDisappearing() {
        assertEquals("bilinmeyen", labelDisplayName("bilinmeyen"))
    }

    @Test
    fun sourceBadges_areTheTurkishWordsFromTheSpec() {
        assertEquals("kayıt", sourceBadge(SampleSource.ENROLL))
        assertEquals("otomatik", sourceBadge(SampleSource.AUTO))
        assertEquals("elle", sourceBadge(SampleSource.MANUAL))
    }
}
