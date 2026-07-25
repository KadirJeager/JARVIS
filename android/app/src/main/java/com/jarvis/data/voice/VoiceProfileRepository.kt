package com.jarvis.data.voice

import com.jarvis.data.net.LabelPatch
import com.jarvis.data.net.NotePatch
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceQualityDto
import com.jarvis.data.net.VoiceSampleDto

/**
 * Data access for the speaker-identity management screen (spec §6).
 *
 * Every mutation is a plain pass-through: the last-anchor guard, the manual cap,
 * idempotency and label validation all live on the server, and re-deriving any of them
 * here would put one rule in two places (spec §9). What this layer DOES own is the
 * tolerant-wire / strict-domain boundary.
 */
class VoiceProfileRepository(private val api: VoiceApi) {

    suspend fun load(): VoiceProfile {
        val dto = api.profile()
        return VoiceProfile(
            counts = VoiceCounts(dto.counts.anchors, dto.counts.auto, dto.counts.manual),
            samples = dto.samples.map { it.toDomain() },
            // A row with no score or no verdict cannot be rendered or corrected;
            // dropping it keeps one bad row from blanking the entire screen.
            history = dto.history.mapNotNull { it.toDomainOrNull() },
            quality = dto.quality.toDomain(),
        )
    }

    /** A null [label] CLEARS the label; the note is never mentioned, so it is untouched. */
    suspend fun setLabel(sampleId: String, label: String?) {
        api.patchLabel(sampleId, LabelPatch(label))
    }

    /** A null [note] CLEARS the note; the label is never mentioned, so it is untouched. */
    suspend fun setNote(sampleId: String, note: String?) {
        api.patchNote(sampleId, NotePatch(note))
    }

    suspend fun deleteSample(sampleId: String) { api.deleteSample(sampleId) }

    suspend fun confirm(entryId: String) { api.confirm(entryId) }

    suspend fun reject(entryId: String) { api.reject(entryId) }

    suspend fun deleteProfile() { api.deleteProfile() }
}

private fun VoiceSampleDto.toDomain() = VoiceSample(
    id = id,
    source = when (source) {
        "enroll" -> SampleSource.ENROLL
        "manual" -> SampleSource.MANUAL
        // Anything else (including a future source we don't know yet) reads as auto
        // rather than crashing a screen whose whole job is to show what IS there.
        else -> SampleSource.AUTO
    },
    ts = ts,
    deviceHint = device_hint,
    label = label,
    note = note,
)

private fun VoiceHistoryDto.toDomainOrNull(): HistoryRow? {
    val s = score ?: return null
    val v = verified ?: return null
    return HistoryRow(
        id = id,
        ts = ts,
        score = s,
        verified = v,
        deviceHint = device_hint,
        trustLevel = trust_level,
        adaptedSampleId = adapted_sample_id,
        correction = when (correction) {
            "confirmed" -> Correction.CONFIRMED
            "rejected" -> Correction.REJECTED
            else -> Correction.NONE
        },
    )
}

private fun VoiceQualityDto.toDomain() = VoiceQuality(
    meanVerifiedScore = mean_verified_score,
    failRate = fail_rate,
    byDevice = by_device.filterValues { it != null }.mapValues { it.value!! },
    byLabel = by_label.filterValues { it != null }.mapValues { it.value!! },
    last10 = trend.last10,
    previous10 = trend.previous10,
)
