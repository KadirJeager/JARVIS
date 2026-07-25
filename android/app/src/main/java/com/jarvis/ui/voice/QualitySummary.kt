package com.jarvis.ui.voice

import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceQuality
import java.util.Locale

enum class TrendDirection { UP, DOWN, FLAT, UNKNOWN }

data class QualitySummary(
    val headline: String,
    val detail: String,
    val trend: TrendDirection,
)

/**
 * Movement smaller than this between the last-10 and previous-10 windows is noise, not
 * a trend. Without a dead band the arrow flips on rounding and the card looks unstable.
 */
private const val TREND_DEAD_BAND = 0.02

/**
 * Turns the server's raw indicators (spec §6.1) into the plain Turkish the status card
 * shows (spec §9).
 *
 * This deliberately does NOT restate the server's accept/adapt thresholds. Copying
 * the server's numeric thresholds here would make the same number live in two places
 * and drift after the calibration pass; the bands below describe how recognition
 * FEELS, and the exact verdict stays where it is enforced.
 */
fun summarize(quality: VoiceQuality, counts: VoiceCounts): QualitySummary {
    if (counts.total == 0) {
        return QualitySummary(
            headline = "Ses kimliği yok",
            detail = "Henüz kayıtlı ses örneğin yok. Ses kaydı yapıldığında burada görünecek.",
            trend = TrendDirection.UNKNOWN,
        )
    }

    val mean = quality.meanVerifiedScore
    if (mean == null) {
        return QualitySummary(
            headline = "Henüz ölçüm yok",
            detail = "${counts.total} örnek kayıtlı, ama daha doğrulanmış bir söyleyiş yok. " +
                "Sesli konuştuğunda buraya skorlar düşmeye başlayacak.",
            trend = TrendDirection.UNKNOWN,
        )
    }

    val headline = when {
        mean >= 0.65 -> "Tanınma güçlü"
        mean >= 0.40 -> "Tanınma orta"
        else -> "Tanınma zayıf"
    }

    val parts = mutableListOf("Doğrulanmış ortalama ${fmt(mean)}")
    quality.failRate?.let { parts += "başarısız oran %${(it * 100).toInt()}" }
    if (quality.byLabel.isNotEmpty()) {
        parts += quality.byLabel.entries
            .sortedBy { it.key }
            .joinToString(", ") { "${labelDisplayName(it.key)}: ${fmt(it.value)}" }
    }

    return QualitySummary(
        headline = headline,
        detail = parts.joinToString(" · "),
        trend = trendOf(quality.last10, quality.previous10),
    )
}

private fun trendOf(last10: Double?, previous10: Double?): TrendDirection {
    // A null previous window means only one window of history exists — that is
    // "not comparable yet", which is different from "no change".
    if (last10 == null || previous10 == null) return TrendDirection.UNKNOWN
    val delta = last10 - previous10
    return when {
        delta > TREND_DEAD_BAND -> TrendDirection.UP
        delta < -TREND_DEAD_BAND -> TrendDirection.DOWN
        else -> TrendDirection.FLAT
    }
}

/** Locale.ROOT: a Turkish-locale device would otherwise render "0,74" and break parity
 *  with the scores logged server-side. */
private fun fmt(v: Double): String = String.format(Locale.ROOT, "%.2f", v)
