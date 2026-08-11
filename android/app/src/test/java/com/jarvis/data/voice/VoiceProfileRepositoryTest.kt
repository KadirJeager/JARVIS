package com.jarvis.data.voice

import com.jarvis.data.net.ChallengeResponse
import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.EnrollRequest
import com.jarvis.data.net.EnrollResponse
import com.jarvis.data.net.LabelPatch
import com.jarvis.data.net.NotePatch
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceCountsDto
import com.jarvis.data.net.VoiceHistoryDto
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceQualityDto
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.net.VoiceTrendDto
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class VoiceProfileRepositoryTest {

    private class FakeVoiceApi(
        var profile: VoiceProfileResponse = VoiceProfileResponse(),
    ) : VoiceApi {
        var patchedLabel: Pair<String, LabelPatch>? = null
        var patchedNote: Pair<String, NotePatch>? = null
        var deletedSample: String? = null
        var confirmed: String? = null
        var rejected: String? = null
        var profileDeleted = false

        override suspend fun profile(): VoiceProfileResponse = profile
        override suspend fun patchLabel(id: String, req: LabelPatch): VoiceSampleDto {
            patchedLabel = id to req
            return VoiceSampleDto(id = id, source = "auto", label = req.label)
        }
        override suspend fun patchNote(id: String, req: NotePatch): VoiceSampleDto {
            patchedNote = id to req
            return VoiceSampleDto(id = id, source = "auto", note = req.note)
        }
        override suspend fun deleteSample(id: String): SampleDeletedResponse {
            deletedSample = id
            return SampleDeletedResponse(id)
        }
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmed = id
            return ConfirmResponse("s-new", already = false)
        }
        override suspend fun reject(id: String): RejectResponse {
            rejected = id
            return RejectResponse(null, already = true)
        }
        override suspend fun deleteProfile(): ProfileDeletedResponse {
            profileDeleted = true
            return ProfileDeletedResponse(true)
        }
        // Unrelated to this repository's contract; VoiceEnrollApiTest covers these.
        override suspend fun challenge(): ChallengeResponse = ChallengeResponse("challenge_created", true)
        override suspend fun enroll(req: EnrollRequest): EnrollResponse = EnrollResponse(0)
    }

    @Test
    fun load_mapsSamplesWithTheirSourceAndCounts() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                counts = VoiceCountsDto(anchors = 2, auto = 1, manual = 0),
                samples = listOf(
                    VoiceSampleDto("s1", "enroll", "t1", "buds", "kulaklik", null),
                    VoiceSampleDto("s2", "auto", null, "unknown", null, null),
                ),
            ),
        )
        val profile = VoiceProfileRepository(api).load()

        assertEquals(2, profile.counts.anchors)
        assertEquals(SampleSource.ENROLL, profile.samples[0].source)
        assertEquals("kulaklik", profile.samples[0].label)
        assertEquals(SampleSource.AUTO, profile.samples[1].source)
        assertNull(profile.samples[1].ts)          // legacy anchors carry no timestamp
    }

    /** An unrecognised source must not crash the screen; it degrades to AUTO. */
    @Test
    fun load_mapsAnUnknownSourceToAuto() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(samples = listOf(VoiceSampleDto("s3", "martian"))),
        )
        assertEquals(SampleSource.AUTO, VoiceProfileRepository(api).load().samples[0].source)
    }

    /**
     * One malformed history row must not blank the whole screen. The server-side
     * backlog already carries "malformed history row -> raw 500"; letting it also
     * break the client would be paying for the same defect twice.
     */
    @Test
    fun load_dropsHistoryRowsMissingScoreOrVerified_andKeepsTheRest() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                history = listOf(
                    VoiceHistoryDto("h1", "t", 0.7, true, "buds", "foreground", "HIGH", "s9", null),
                    VoiceHistoryDto("h2", "t", null, true),        // no score -> dropped
                    VoiceHistoryDto("h3", "t", 0.2, null),         // no verdict -> dropped
                    VoiceHistoryDto("h4", "t", 0.4, false, correction = "rejected"),
                ),
            ),
        )
        val rows = VoiceProfileRepository(api).load().history

        assertEquals(listOf("h1", "h4"), rows.map { it.id })
        assertEquals(Correction.NONE, rows[0].correction)
        assertEquals(Correction.REJECTED, rows[1].correction)
        assertEquals(0.4, rows[1].score, 1e-9)
    }

    @Test
    fun load_mapsQualityIncludingNulls() = runBlocking {
        val api = FakeVoiceApi(
            VoiceProfileResponse(
                quality = VoiceQualityDto(
                    mean_verified_score = 0.71, fail_rate = 0.2,
                    by_device = mapOf("buds" to 0.68, "bozuk" to null),
                    by_label = mapOf("kulaklik" to 0.72),
                    trend = VoiceTrendDto(0.70, null),
                ),
            ),
        )
        val q = VoiceProfileRepository(api).load().quality

        assertEquals(0.71, q.meanVerifiedScore!!, 1e-9)
        assertEquals(0.68, q.byDevice["buds"]!!, 1e-9)
        assertTrue("null ortalamalar atılmalı", "bozuk" !in q.byDevice)
        assertNull(q.previous10)
    }

    /**
     * "Only the label" is the whole claim: the server CLEARS any field the body mentions
     * with a null, so a label edit that also carried the note would wipe the user's note.
     * Asserting the note request was never made is what makes the test name true.
     */
    @Test
    fun setLabel_sendsOnlyTheLabelField() = runBlocking {
        val api = FakeVoiceApi()
        VoiceProfileRepository(api).setLabel("s1", "yorgun")
        assertEquals("s1", api.patchedLabel!!.first)
        assertEquals("yorgun", api.patchedLabel!!.second.label)
        assertNull("etiket düzenlemesi note'a dokunmamalı", api.patchedNote)
    }

    /** Clearing the label is a real user action ("Etiketi kaldır"), not just an edit. */
    @Test
    fun setLabel_null_clearsTheLabelAndStillLeavesTheNoteAlone() = runBlocking {
        val api = FakeVoiceApi()
        VoiceProfileRepository(api).setLabel("s1", null)
        assertEquals("s1", api.patchedLabel!!.first)
        assertNull(api.patchedLabel!!.second.label)
        assertNull("etiket temizleme note'a dokunmamalı", api.patchedNote)
    }

    @Test
    fun setNote_sendsOnlyTheNoteField() = runBlocking {
        val api = FakeVoiceApi()
        VoiceProfileRepository(api).setNote("s1", null)
        assertEquals("s1", api.patchedNote!!.first)
        assertNull(api.patchedNote!!.second.note)
        assertNull("not düzenlemesi label'a dokunmamalı", api.patchedLabel)
    }

    @Test
    fun mutations_reachTheirEndpoints() = runBlocking {
        val api = FakeVoiceApi()
        val repo = VoiceProfileRepository(api)
        repo.deleteSample("s1")
        repo.confirm("h1")
        repo.reject("h2")
        repo.deleteProfile()
        assertEquals("s1", api.deletedSample)
        assertEquals("h1", api.confirmed)
        assertEquals("h2", api.rejected)
        assertTrue(api.profileDeleted)
    }
}
