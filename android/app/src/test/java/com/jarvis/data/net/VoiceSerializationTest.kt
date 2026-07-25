package com.jarvis.data.net

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The backend contract is fixed (brain/app/voice_manage.py). These tests pin the exact
 * JSON the server produces, including the shapes that a single shared model would get
 * wrong.
 */
class VoiceSerializationTest {

    private val json = Json { ignoreUnknownKeys = true }

    @Test
    fun profileResponse_decodesFullShape() {
        val raw = """
            {"counts":{"anchors":7,"auto":2,"manual":1},
             "samples":[{"id":"s1","source":"enroll","ts":"2026-07-25T00:00:00Z",
                         "device_hint":"buds","label":"kulaklik","note":"ilk kayit"}],
             "history":[{"id":"h1","ts":"2026-07-25T01:00:00Z","score":0.71,"verified":true,
                         "device_hint":"buds","presence":"foreground","trust_level":"HIGH",
                         "adapted_sample_id":"s9","correction":null}],
             "quality":{"mean_verified_score":0.71,"fail_rate":0.2,
                        "by_device":{"buds":0.68},"by_label":{"kulaklik":0.72},
                        "trend":{"last10":0.70,"previous10":0.64}}}
        """.trimIndent()

        val p = json.decodeFromString<VoiceProfileResponse>(raw)

        assertEquals(7, p.counts.anchors)
        assertEquals(2, p.counts.auto)
        assertEquals(1, p.counts.manual)
        assertEquals("s1", p.samples[0].id)
        assertEquals("enroll", p.samples[0].source)
        assertEquals("kulaklik", p.samples[0].label)
        assertEquals("ilk kayit", p.samples[0].note)
        assertEquals("h1", p.history[0].id)
        assertEquals(0.71, p.history[0].score!!, 1e-9)
        assertEquals(true, p.history[0].verified)
        assertEquals("s9", p.history[0].adapted_sample_id)
        assertNull(p.history[0].correction)
        assertEquals(0.71, p.quality.mean_verified_score!!, 1e-9)
        assertEquals(0.68, p.quality.by_device["buds"]!!, 1e-9)
        assertEquals(0.70, p.quality.trend.last10!!, 1e-9)
    }

    /**
     * An empty profile is a legitimate state (nothing enrolled yet): every quality figure
     * is null and both maps are empty. The screen must be reachable then.
     */
    @Test
    fun profileResponse_decodesEmptyProfile() {
        val raw = """
            {"counts":{"anchors":0,"auto":0,"manual":0},"samples":[],"history":[],
             "quality":{"mean_verified_score":null,"fail_rate":null,
                        "by_device":{},"by_label":{},
                        "trend":{"last10":null,"previous10":null}}}
        """.trimIndent()

        val p = json.decodeFromString<VoiceProfileResponse>(raw)

        assertTrue(p.samples.isEmpty())
        assertTrue(p.history.isEmpty())
        assertNull(p.quality.mean_verified_score)
        assertTrue(p.quality.by_device.isEmpty())
        assertNull(p.quality.trend.previous10)
    }

    /**
     * `_project()` in voice_manage.py uses row.get(f), so ANY absent field arrives as
     * JSON null; `_normalize_sample` additionally produces ts=None for pre-3d bare-vector
     * anchors. A non-nullable `ts` would blow up on Kadir's own legacy rows.
     */
    @Test
    fun sample_toleratesNullTsAndNullLabelAndNullNote() {
        val raw = """{"id":"s2","source":"auto","ts":null,"device_hint":"unknown",
                      "label":null,"note":null}"""
        val s = json.decodeFromString<VoiceSampleDto>(raw)
        assertEquals("s2", s.id)
        assertNull(s.ts)
        assertNull(s.label)
        assertNull(s.note)
    }

    /**
     * The two DELETE endpoints put DIFFERENT TYPES under the same "deleted" key: sample
     * deletion returns the id (string), profile deletion returns true (bool). One shared
     * model cannot decode both.
     */
    @Test
    fun deleteResponses_haveDifferentTypesUnderTheSameKey() {
        val sample = json.decodeFromString<SampleDeletedResponse>("""{"deleted":"s1"}""")
        assertEquals("s1", sample.deleted)

        val profile = json.decodeFromString<ProfileDeletedResponse>("""{"deleted":true}""")
        assertEquals(true, profile.deleted)
    }

    @Test
    fun correctionResponses_decodeNullableIdsAndAlreadyFlag() {
        val c = json.decodeFromString<ConfirmResponse>(
            """{"added_sample_id":"s5","already":false}""",
        )
        assertEquals("s5", c.added_sample_id)
        assertEquals(false, c.already)

        val r = json.decodeFromString<RejectResponse>(
            """{"removed_sample_id":null,"already":true}""",
        )
        assertNull(r.removed_sample_id)
        assertEquals(true, r.already)
    }

    /**
     * PATCH must be able to send an explicit null to CLEAR a label — the server
     * distinguishes "absent" from "null" via model_fields_set, and kotlinx by default
     * omits nulls, which would silently turn "clear the label" into a no-op.
     */
    @Test
    fun samplePatch_encodesExplicitNullsSoAClearIsNotSilentlyDropped() {
        val encoded = VoiceApiJson.encodePatch(SamplePatchRequest(label = null, note = null))
        assertTrue("label açıkça null gitmeli: $encoded", encoded.contains("\"label\":null"))
        assertTrue("note açıkça null gitmeli: $encoded", encoded.contains("\"note\":null"))
    }

    /** A malformed history row must decode, not throw: the repository drops it later. */
    @Test
    fun history_toleratesMissingScoreAndVerified() {
        val h = json.decodeFromString<VoiceHistoryDto>("""{"id":"h9","ts":null}""")
        assertEquals("h9", h.id)
        assertNull(h.score)
        assertNull(h.verified)
    }
}
