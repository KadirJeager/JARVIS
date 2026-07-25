package com.jarvis.data.voice

/** Where a gallery sample came from (spec §4.1). Wire values: enroll/auto/manual. */
enum class SampleSource { ENROLL, AUTO, MANUAL }

/** Whether the user has already ruled on a history row (spec §6). */
enum class Correction { NONE, CONFIRMED, REJECTED }

data class VoiceCounts(val anchors: Int, val auto: Int, val manual: Int) {
    val total: Int get() = anchors + auto + manual
}

data class VoiceSample(
    val id: String,
    val source: SampleSource,
    val ts: String?,
    val deviceHint: String?,
    val label: String?,
    val note: String?,
)

/**
 * One verified utterance. Unlike the wire model, `score` and `verified` are non-null
 * here: rows that lack them are dropped at the repository boundary, so nothing
 * downstream has to ask "what if there is no verdict".
 */
data class HistoryRow(
    val id: String,
    val ts: String?,
    val score: Double,
    val verified: Boolean,
    val deviceHint: String?,
    val trustLevel: String?,
    val adaptedSampleId: String?,
    val correction: Correction,
)

data class VoiceQuality(
    val meanVerifiedScore: Double?,
    val failRate: Double?,
    val byDevice: Map<String, Double>,
    val byLabel: Map<String, Double>,
    val last10: Double?,
    val previous10: Double?,
)

data class VoiceProfile(
    val counts: VoiceCounts,
    val samples: List<VoiceSample>,
    val history: List<HistoryRow>,
    val quality: VoiceQuality,
) {
    val isEmpty: Boolean get() = samples.isEmpty() && history.isEmpty()
}
