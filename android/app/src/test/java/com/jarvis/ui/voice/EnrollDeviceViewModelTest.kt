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
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceSampleDto
import kotlinx.coroutines.CancellationException
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
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response

/**
 * Task 9: "Bu cihazı tanıt" enrollment flow. [EnrollDeviceViewModel.proceedToRecording]
 * is a deliberate, user-driven gate (controller decision 5) -- the ViewModel cannot
 * observe the server-side liveness grant directly, so it waits for the user to confirm
 * they repeated the spoken code before recording anything. Every test that needs to
 * reach past [EnrollState.WaitingForSpokenCode] therefore calls both [EnrollDeviceViewModel.start]
 * and [EnrollDeviceViewModel.proceedToRecording] explicitly.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class EnrollDeviceViewModelTest {

    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)
    @After fun tearDown() = Dispatchers.resetMain()

    private fun http(code: Int) = HttpException(
        Response.error<Any>(code, "{}".toResponseBody("application/json".toMediaType())),
    )

    /** Only [challenge] and [enroll] matter to this ViewModel; every other member of
     *  [VoiceApi] is unused here and throws if accidentally called. */
    private class FakeVoiceApi(
        val anchors: Int = 0,
        val codeSpoken: Boolean = true,
        val challengeStatus: Int? = null,
        val enrollStatus: Int? = null,
    ) : VoiceApi {
        var challengeCalls = 0
        var enrollCalls = 0
        var lastEnroll: EnrollRequest? = null

        override suspend fun challenge(): ChallengeResponse {
            challengeCalls++
            challengeStatus?.let { throw HttpException(
                Response.error<Any>(it, "{}".toResponseBody("application/json".toMediaType())),
            ) }
            return ChallengeResponse("challenge_created", codeSpoken)
        }

        override suspend fun enroll(req: EnrollRequest): EnrollResponse {
            enrollCalls++
            lastEnroll = req
            enrollStatus?.let { throw HttpException(
                Response.error<Any>(it, "{}".toResponseBody("application/json".toMediaType())),
            ) }
            return EnrollResponse(anchors)
        }

        override suspend fun profile(): VoiceProfileResponse = throw UnsupportedOperationException()
        override suspend fun patchLabel(id: String, req: LabelPatch): VoiceSampleDto =
            throw UnsupportedOperationException()
        override suspend fun patchNote(id: String, req: NotePatch): VoiceSampleDto =
            throw UnsupportedOperationException()
        override suspend fun deleteSample(id: String): SampleDeletedResponse =
            throw UnsupportedOperationException()
        override suspend fun confirm(id: String): ConfirmResponse = throw UnsupportedOperationException()
        override suspend fun reject(id: String): RejectResponse = throw UnsupportedOperationException()
        override suspend fun deleteProfile(): ProfileDeletedResponse = throw UnsupportedOperationException()
    }

    private class FakeRecorder : ClipRecorder {
        var lastCount: Int? = null
        var lastSeconds: Double? = null
        override suspend fun record(count: Int, seconds: Double): List<ByteArray> {
            lastCount = count
            lastSeconds = seconds
            return List(count) { byteArrayOf(1, 2, 3) }
        }
    }

    private class FailingRecorder : ClipRecorder {
        override suspend fun record(count: Int, seconds: Double): List<ByteArray> =
            throw IllegalStateException("AudioRecord failed to initialize (mic busy or unavailable)")
    }

    private class CancellingRecorder : ClipRecorder {
        override suspend fun record(count: Int, seconds: Double): List<ByteArray> =
            throw CancellationException("scope torn down")
    }

    @Test
    fun happyPathReachesDoneWithAnchorCount() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(anchors = 10), FakeRecorder(), "android-Pixel 10 Pro")
        vm.start()
        advanceUntilIdle()
        assertEquals(EnrollState.WaitingForSpokenCode, vm.state.value)

        vm.proceedToRecording()
        advanceUntilIdle()
        assertEquals(EnrollState.Done(10), vm.state.value)
    }

    @Test
    fun missingGrantTellsTheUserToRepeatTheCode() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(enrollStatus = 409), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        vm.proceedToRecording()
        advanceUntilIdle()

        val failed = vm.state.value as EnrollState.Failed
        assertTrue(failed.message.contains("kod"))
    }

    @Test
    fun spoofRejectionSaysSoPlainly() = runTest(dispatcher) {
        // Retrying identically will not help, so the message must not read as a
        // transient error.
        val vm = EnrollDeviceViewModel(FakeVoiceApi(enrollStatus = 422), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        vm.proceedToRecording()
        advanceUntilIdle()

        val failed = vm.state.value as EnrollState.Failed
        assertTrue(failed.message.contains("sahte"))
    }

    @Test
    fun cmUnavailableSaysToTryAgainLater() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(enrollStatus = 503), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        vm.proceedToRecording()
        advanceUntilIdle()

        val failed = vm.state.value as EnrollState.Failed
        assertTrue(failed.message.contains("Ses doğrulaması"))
    }

    @Test
    fun deviceHintIsTheSameStringTheVoiceBridgeSends() = runTest(dispatcher) {
        // One name, one unit: the gallery's channel tag must match what the live
        // path writes (JarvisApp.kt: "android-" + Build.MODEL), or enrollment adds
        // anchors under a channel label no utterance will ever carry.
        val api = FakeVoiceApi(anchors = 1)
        val vm = EnrollDeviceViewModel(api, FakeRecorder(), "android-Pixel 10 Pro")
        vm.start()
        advanceUntilIdle()
        vm.proceedToRecording()
        advanceUntilIdle()

        assertEquals("android-Pixel 10 Pro", api.lastEnroll!!.device_hint)
    }

    @Test
    fun recorderFailureDoesNotStrandTheUiInRecording() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(), FailingRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        vm.proceedToRecording()
        advanceUntilIdle()

        assertTrue(vm.state.value is EnrollState.Failed)
    }

    /**
     * Controller decision 3: the server only speaks the code over an OPEN voice
     * bridge. If it could not, telling the user to retry the exact same tap would be
     * useless -- the message must point at the actual precondition instead.
     */
    @Test
    fun codeNotSpokenTellsTheUserToStartTheCallFirst() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(codeSpoken = false), FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()

        val failed = vm.state.value as EnrollState.Failed
        assertEquals(
            "Kodu duyabilmen için önce sesli aramayı başlat, sonra tekrar dene.",
            failed.message,
        )
    }

    /**
     * Controller decision 5: [EnrollDeviceViewModel.proceedToRecording] is the only
     * way out of [EnrollState.WaitingForSpokenCode] -- there is no timer, and calling it
     * before that state is reached must do nothing (no premature recording, no crash).
     */
    @Test
    fun proceedToRecordingIsANoOpBeforeWaitingForSpokenCode() = runTest(dispatcher) {
        val api = FakeVoiceApi(anchors = 1)
        val vm = EnrollDeviceViewModel(api, FakeRecorder(), "x")

        vm.proceedToRecording()
        advanceUntilIdle()

        assertEquals(EnrollState.Idle, vm.state.value)
        assertEquals(0, api.enrollCalls)
    }

    /** The transition itself: synchronous on tap, same as the rest of this codebase's
     *  "mark busy synchronously, resolve async" ViewModel convention. */
    @Test
    fun proceedToRecordingAdvancesSynchronouslyThenReachesDone() = runTest(dispatcher) {
        val api = FakeVoiceApi(anchors = 3)
        val vm = EnrollDeviceViewModel(api, FakeRecorder(), "x")
        vm.start()
        advanceUntilIdle()
        assertEquals(EnrollState.WaitingForSpokenCode, vm.state.value)

        vm.proceedToRecording()
        assertEquals(EnrollState.Recording, vm.state.value) // set before the coroutine resumes
        advanceUntilIdle()
        assertEquals(EnrollState.Done(3), vm.state.value)
    }

    /**
     * Precedent: JarvisApi.chat, WatchPairing, ChatViewModel all once rewrapped
     * coroutine cancellation as a domain failure. A recorder that throws
     * [CancellationException] (scope torn down mid-recording) must leave the state
     * exactly where it was left synchronously -- NOT recast into [EnrollState.Failed].
     */
    @Test
    fun cancellationDuringRecordingIsNotRecastAsFailed() = runTest(dispatcher) {
        val vm = EnrollDeviceViewModel(FakeVoiceApi(), CancellingRecorder(), "x")
        vm.start()
        advanceUntilIdle()

        vm.proceedToRecording()
        advanceUntilIdle()

        assertEquals(EnrollState.Recording, vm.state.value)
    }
}
