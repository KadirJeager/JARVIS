package com.jarvis.data.net

import kotlinx.serialization.Serializable

/**
 * Wire models for the speaker-identity management contract (brain/app/voice_manage.py,
 * spec §6). Field names MUST match the JSON exactly — the backend is fixed.
 *
 * NOTE: `vec` is deliberately absent from every model here. Embeddings never leave the
 * server (spec §6) and the client has no use for them; not declaring the field is the
 * cheapest way to guarantee we never start asking for one.
 *
 * Nullability is not defensiveness for its own sake: `_project()` builds responses with
 * `row.get(field)`, so any absent field arrives as JSON null, and `_normalize_sample`
 * produces `ts=None` for pre-3d bare-vector anchors.
 */
@Serializable
data class VoiceCountsDto(
    val anchors: Int = 0,
    val auto: Int = 0,
    val manual: Int = 0,
)

@Serializable
data class VoiceSampleDto(
    val id: String,
    val source: String,
    val ts: String? = null,
    val device_hint: String? = null,
    val label: String? = null,
    val note: String? = null,
)

@Serializable
data class VoiceHistoryDto(
    val id: String,
    val ts: String? = null,
    val score: Double? = null,
    val verified: Boolean? = null,
    val device_hint: String? = null,
    val presence: String? = null,
    val trust_level: String? = null,
    val adapted_sample_id: String? = null,
    val correction: String? = null,
)

@Serializable
data class VoiceTrendDto(
    val last10: Double? = null,
    val previous10: Double? = null,
)

@Serializable
data class VoiceQualityDto(
    val mean_verified_score: Double? = null,
    val fail_rate: Double? = null,
    val by_device: Map<String, Double?> = emptyMap(),
    val by_label: Map<String, Double?> = emptyMap(),
    val trend: VoiceTrendDto = VoiceTrendDto(),
)

@Serializable
data class VoiceProfileResponse(
    val counts: VoiceCountsDto = VoiceCountsDto(),
    val samples: List<VoiceSampleDto> = emptyList(),
    val history: List<VoiceHistoryDto> = emptyList(),
    val quality: VoiceQualityDto = VoiceQualityDto(),
)

/**
 * PATCH bodies. The server tells "omitted" from "explicitly null" via Pydantic's
 * model_fields_set: an omitted field is left alone, an explicit null CLEARS it.
 *
 * These are deliberately ONE FIELD EACH AND DEFAULT-FREE, and that is the whole point:
 *  - No default ⇒ the generated serializer writes the field unconditionally, so
 *    `label = null` really reaches the wire as `"label":null` and a "clear the label"
 *    is not silently downgraded to a no-op. (With a `= null` default kotlinx emits
 *    `shouldEncodeElementDefault(...) || value != null`, so under the default
 *    `encodeDefaults = false` the null would be DROPPED and the body would be `{}`.)
 *  - One field ⇒ the sibling field is not even declared, so patching a label can never
 *    carry an incidental `"note":null` that would make the server WIPE the note.
 *
 * Do not merge these back into one model with defaults: that combination is exactly the
 * pair of bugs above, and no `Json` flag can satisfy both at once.
 */
@Serializable
data class LabelPatch(val label: String?)

@Serializable
data class NotePatch(val note: String?)

@Serializable
data class SampleDeletedResponse(val deleted: String)

/** Same key, different type from [SampleDeletedResponse] — deliberately separate. */
@Serializable
data class ProfileDeletedResponse(val deleted: Boolean)

@Serializable
data class ConfirmResponse(
    val added_sample_id: String? = null,
    val already: Boolean = false,
)

@Serializable
data class RejectResponse(
    val removed_sample_id: String? = null,
    val already: Boolean = false,
)
