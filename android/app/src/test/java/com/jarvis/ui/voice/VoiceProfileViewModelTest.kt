package com.jarvis.ui.voice

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
import com.jarvis.data.net.VoiceSampleDto
import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

@OptIn(ExperimentalCoroutinesApi::class)
class VoiceProfileViewModelTest {

    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun http(code: Int, detail: String) = HttpException(
        Response.error<Any>(
            code,
            """{"detail":"$detail"}""".toResponseBody("application/json".toMediaType()),
        ),
    )

    private class FakeVoiceApi : VoiceApi {
        var response = VoiceProfileResponse(
            counts = VoiceCountsDto(anchors = 1),
            samples = listOf(VoiceSampleDto("s1", "enroll")),
            history = listOf(VoiceHistoryDto("h1", "t", 0.7, true)),
        )
        var profileCalls = 0
        var deleteSampleError: Throwable? = null
        var confirmError: Throwable? = null
        var confirmCalls = 0
        var profileDeleted = false

        override suspend fun profile(): VoiceProfileResponse {
            profileCalls++
            return response
        }
        override suspend fun patchLabel(id: String, req: LabelPatch) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun patchNote(id: String, req: NotePatch) =
            VoiceSampleDto(id, "auto", note = req.note)
        override suspend fun deleteSample(id: String): SampleDeletedResponse {
            deleteSampleError?.let { throw it }
            return SampleDeletedResponse(id)
        }
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmCalls++
            confirmError?.let { throw it }
            return ConfirmResponse("s-new")
        }
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile(): ProfileDeletedResponse {
            profileDeleted = true
            return ProfileDeletedResponse(true)
        }
        // Unrelated to this ViewModel's contract; VoiceEnrollApiTest covers these.
        override suspend fun challenge(): ChallengeResponse = ChallengeResponse("challenge_created", true)
        override suspend fun enroll(req: EnrollRequest): EnrollResponse = EnrollResponse(0)
    }

    private fun vm(api: FakeVoiceApi) = VoiceProfileViewModel(VoiceProfileRepository(api))

    /** The screen is gated: nothing is fetched before the biometric prompt succeeds. */
    @Test
    fun startsLocked_andFetchesNothingUntilUnlocked() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        advanceUntilIdle()
        assertEquals(GatePhase.CHECKING, model.state.value.gate)
        assertEquals(0, api.profileCalls)
    }

    @Test
    fun unlocking_loadsTheProfileAndBuildsTheSummary() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        assertEquals(GatePhase.UNLOCKED, model.state.value.gate)
        assertEquals(1, api.profileCalls)
        assertNotNull(model.state.value.profile)
        assertNotNull(model.state.value.summary)
        assertFalse(model.state.value.loading)
    }

    @Test
    fun unlockFailure_landsOnDenied_withATurkishReason_andNoFetch() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlockFailed("iptal edildi")
        advanceUntilIdle()

        assertEquals(GatePhase.DENIED, model.state.value.gate)
        assertTrue(model.state.value.error!!.contains("Kilit açılamadı"))
        assertEquals(0, api.profileCalls)
    }

    /**
     * Mutations do not patch local state: whether confirm promoted a sample in place or
     * appended a new one, whether the cap refused it, whether reject actually removed
     * anything -- all of that is server state (spec §9). Re-reading is the only honest
     * way to show the result.
     */
    @Test
    fun aSuccessfulMutation_reloadsFromTheServer() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()
        assertEquals(1, api.profileCalls)

        model.confirm("h1")
        advanceUntilIdle()

        assertEquals(2, api.profileCalls)
        assertNull(model.state.value.mutatingId)
    }

    @Test
    fun aMutationMarksItsRowBusy_thenClearsIt() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteSample("s1")
        assertEquals("s1", model.state.value.mutatingId)   // set synchronously
        advanceUntilIdle()
        assertNull(model.state.value.mutatingId)
    }

    /**
     * The endpoints are idempotent, but two mutations in flight race each other's
     * reload and the user sees a row blink out. One at a time also stops a double tap
     * from burning a manual-cap slot.
     */
    @Test
    fun aSecondMutationIsIgnoredWhileOneIsInFlight() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        model.confirm("h1")      // double tap, before the first resolves
        advanceUntilIdle()

        assertEquals(1, api.confirmCalls)
    }

    /** The server owns the rule AND its wording; the client must not paraphrase. */
    @Test
    fun aRefusedDeletion_showsTheServersOwnSentence() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.deleteSampleError = http(400, "Son çapa silinemez: çapasız profil ses doğrulayamaz.")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteSample("s1")
        advanceUntilIdle()

        assertEquals(
            "Son çapa silinemez: çapasız profil ses doğrulayamaz.",
            model.state.value.error,
        )
        assertNull(model.state.value.mutatingId)
    }

    @Test
    fun aCapRefusal_showsTheServersLiveNumbers() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "Elle eklenen örnek sınırı dolu (5/5).")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        advanceUntilIdle()

        assertTrue(model.state.value.error!!.contains("(5/5)"))
    }

    /** A failed mutation must still leave the screen usable and retryable. */
    @Test
    fun afterAFailedMutation_anotherMutationIsStillAccepted() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "olmaz")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.confirm("h1")
        advanceUntilIdle()
        api.confirmError = null
        model.confirm("h1")
        advanceUntilIdle()

        assertEquals(2, api.confirmCalls)
        assertNull(model.state.value.error)
    }

    @Test
    fun loadFailure_showsTheServerMessage_andLeavesTheScreenRetryable() = runTest(dispatcher) {
        val api = object : VoiceApi by FakeVoiceApi() {
            override suspend fun profile(): VoiceProfileResponse =
                throw HttpException(
                    Response.error<Any>(
                        502,
                        """{"detail":"İşlem şu anda yapılamıyor (altyapı hatası). Az sonra tekrar dene."}"""
                            .toResponseBody("application/json".toMediaType()),
                    ),
                )
        }
        val model = VoiceProfileViewModel(VoiceProfileRepository(api))
        model.onUnlocked()
        advanceUntilIdle()

        assertTrue(model.state.value.error!!.contains("altyapı hatası"))
        assertFalse(model.state.value.loading)
        assertNull(model.state.value.profile)
    }

    @Test
    fun deletingTheProfile_marksItDeleted_soTheHostCanLeaveTheScreen() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()

        model.deleteProfile()
        advanceUntilIdle()

        assertTrue(api.profileDeleted)
        assertTrue(model.state.value.deleted)
    }

    @Test
    fun dismissError_clearsIt() = runTest(dispatcher) {
        val api = FakeVoiceApi()
        api.confirmError = http(400, "olmaz")
        val model = vm(api)
        model.onUnlocked()
        advanceUntilIdle()
        model.confirm("h1")
        advanceUntilIdle()
        assertNotNull(model.state.value.error)

        model.dismissError()
        assertNull(model.state.value.error)
    }
}
