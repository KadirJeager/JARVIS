package com.jarvis

import android.content.Context
import android.view.WindowManager
import androidx.compose.ui.test.assertIsDisplayed
import androidx.lifecycle.Lifecycle
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performClick
import androidx.fragment.app.FragmentActivity
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.BiometricGate
import com.jarvis.data.net.ApiSet
import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto
import com.jarvis.data.net.ApprovalsResponse
import com.jarvis.data.net.ConversationDeletedResponse
import com.jarvis.data.net.ConversationsApi
import com.jarvis.data.net.ConversationsResponse
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.ConfirmResponse
import com.jarvis.data.net.FcmApi
import com.jarvis.data.net.FcmRegisterResponse
import com.jarvis.data.net.FcmTokenRequest
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import com.jarvis.data.net.LabelPatch
import com.jarvis.data.net.NotePatch
import com.jarvis.data.net.ProfileDeletedResponse
import com.jarvis.data.net.RejectResponse
import com.jarvis.data.net.SampleDeletedResponse
import com.jarvis.data.net.VoiceApi
import com.jarvis.data.net.VoiceProfileResponse
import com.jarvis.data.net.VoiceSampleDto
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Pins the production wiring that no other test touches: does tapping "Ses kimliğim" in
 * the REAL [MainActivity] actually invoke the REAL [com.jarvis.AppContainer.biometricGate],
 * and does every entry re-arm it (Task 9 fix-round-1 Critical 1)?
 *
 * [VoiceProfileFlowTest] and [com.jarvis.ui.voice.VoiceProfileScreenTest] both drive the
 * screen/ViewModel directly and never touch `MainActivity`'s `openVoiceProfile()` — so
 * neither would notice if the Activity stopped calling the gate at all. This test installs
 * fakes into the real [JarvisApp] container BEFORE [MainActivity] launches (the DI seam
 * added for exactly this purpose) and drives the real Activity end to end.
 *
 * The API set is faked too. A fake [AuthClient] alone puts the app in a signed-in state,
 * and the real repositories then fire live requests at the DEPLOYED backend
 * (`GET /api/history`, `GET /api/voice/profile`); read-only and 401-bound, but a test
 * suite must not reach production at all. [AppContainer]'s `apis` parameter closes that.
 *
 * The container is restored in [tearDown]: instrumented tests share one process, so a
 * leaked fake `AuthClient` would leave a LATER test (e.g. `SmokeTest`) signed in against
 * fakes it never asked for — green for reasons that have nothing to do with what it
 * asserts.
 */
@RunWith(AndroidJUnit4::class)
class VoiceGateWiringTest {

    @get:Rule val compose = createEmptyComposeRule()

    private class FakeAuthClient(private val token: String = "fake-token") : AuthClient {
        override fun currentToken(): String? = token
        override suspend fun signIn(activityContext: Context): Result<String> =
            Result.success(token)
        override suspend fun silentSignIn(): Result<String> = Result.success(token)
    }

    /** Empty but well-formed answers: this test is about the gate, not about content. */
    private class FakeConversationsApi : ConversationsApi {
        override suspend fun list() = ConversationsResponse(emptyList())
        override suspend fun delete(sessionId: String) = ConversationDeletedResponse(sessionId)
    }

    /**
     * The push path is wired into [AppContainer] like every other API, so this test has to
     * supply one. It answers locally rather than reaching the deployed backend: a test
     * suite must not talk to production at all.
     */
    private class FakeFcmApi : FcmApi {
        override suspend fun register(req: FcmTokenRequest) = FcmRegisterResponse(true)
    }

    /**
     * Approvals are wired into [AppContainer] like every other API, so this test has to
     * supply one. It answers "no approvals" rather than reaching the deployed backend: a
     * test suite must not talk to production at all.
     */
    private class FakeApprovalApi : ApprovalApi {
        override suspend fun list() = ApprovalsResponse(emptyList())
        override suspend fun get(id: String) = ApprovalDto(id = id, title = "t", status = "pending")
        override suspend fun approve(id: String) = ApprovalDecisionDto("approved", null, false)
        override suspend fun reject(id: String) = ApprovalDecisionDto("rejected", null, false)
    }

    private class FakeChatApi : JarvisApi {
        override suspend fun chat(req: ChatRequest) = ChatResponse("")
        override suspend fun history(sessionId: String) = HistoryResponse(emptyList())
    }

    private class FakeVoiceApi : VoiceApi {
        override suspend fun profile() = VoiceProfileResponse()
        override suspend fun patchLabel(id: String, req: LabelPatch) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun patchNote(id: String, req: NotePatch) =
            VoiceSampleDto(id, "auto", note = req.note)
        override suspend fun deleteSample(id: String) = SampleDeletedResponse(id)
        override suspend fun confirm(id: String) = ConfirmResponse("s-new")
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile() = ProfileDeletedResponse(true)
    }

    /**
     * Hand the process back a real container. Without this the fakes installed below
     * outlive this class and quietly change what every later test in the run is measuring.
     */
    @After
    fun tearDown() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        app.container = AppContainer(app)
    }

    /**
     * Records every [prompt] call and lets the test resolve it on demand — a real system
     * `BiometricPrompt` sheet cannot be driven from an instrumented test on a lockless
     * emulator (see [com.jarvis.data.auth.BiometricGateTest]), so this stands in for it.
     */
    private class RecordingBiometricGate : BiometricGate {
        var promptCount = 0
            private set
        private var pending: ((Result<Unit>) -> Unit)? = null

        override fun isAvailable(): Boolean = true

        override fun prompt(activity: FragmentActivity, onResult: (Result<Unit>) -> Unit) {
            promptCount++
            pending = onResult
        }

        fun resolveSuccess() {
            val callback = checkNotNull(pending) { "prompt() was not called" }
            pending = null
            callback(Result.success(Unit))
        }
    }

    @Test
    fun openingTheVoiceScreen_promptsTheRealGate_andReLocksOnEveryEntry() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        val fakeGate = RecordingBiometricGate()
        app.container = AppContainer(
            app,
            authManager = FakeAuthClient(),
            biometricGate = fakeGate,
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi(), FakeFcmApi()),
        )

        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()
            compose.onNodeWithTag("open_voice_profile").performClick()
            compose.waitForIdle()

            // The real gate was actually asked, and the locked placeholder -- not the
            // profile -- is what's on screen while its callback is still pending. This is
            // exactly what "replace prompt(...) with a direct onUnlocked()" must break.
            assertEquals(1, fakeGate.promptCount)
            compose.onNodeWithTag("voice_locked").assertIsDisplayed()

            fakeGate.resolveSuccess()
            compose.waitForIdle()

            compose.onNodeWithTag("voice_back").performClick()
            compose.waitForIdle()
            compose.onNodeWithTag("open_voice_profile").performClick()
            compose.waitForIdle()

            // Second entry: a fresh prompt, and the screen re-locks instead of showing
            // the first visit's stale content. This is exactly what deleting the
            // onGateRequested() call must break.
            assertEquals(2, fakeGate.promptCount)
            compose.onNodeWithTag("voice_locked").assertIsDisplayed()
            compose.onNodeWithTag("voice_list").assertDoesNotExist()
        }
    }

    /**
     * Saha açığı (handoff, 26 Tem 2026): backgrounding the app on the voice screen and
     * coming back showed the whole profile WITHOUT a new prompt, and the recents
     * preview snapshotted the content. Coming back must re-lock + re-prompt, and the
     * window must carry FLAG_SECURE while this screen is showing (and only then).
     */
    @Test
    fun backgroundingTheVoiceScreen_reLocksReprompts_andWindowIsSecureOnlyThere() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        val fakeGate = RecordingBiometricGate()
        app.container = AppContainer(
            app,
            authManager = FakeAuthClient(),
            biometricGate = fakeGate,
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi(), FakeFcmApi()),
        )

        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            fun windowSecure(): Boolean {
                var secure = false
                scenario.onActivity {
                    secure = (it.window.attributes.flags and
                        WindowManager.LayoutParams.FLAG_SECURE) != 0
                }
                return secure
            }

            compose.waitForIdle()
            assertEquals(false, windowSecure()) // chat must stay screenshotable

            compose.onNodeWithTag("open_voice_profile").performClick()
            compose.waitForIdle()
            assertEquals(1, fakeGate.promptCount)
            assertEquals(true, windowSecure()) // recents preview must be blank here
            fakeGate.resolveSuccess()
            compose.waitForIdle()

            // Backgrounding and returning — the exact saha gesture.
            scenario.moveToState(Lifecycle.State.CREATED)
            scenario.moveToState(Lifecycle.State.RESUMED)
            compose.waitForIdle()

            // A fresh prompt was demanded and the content is locked until it resolves.
            assertEquals(2, fakeGate.promptCount)
            compose.onNodeWithTag("voice_locked").assertIsDisplayed()
            compose.onNodeWithTag("voice_list").assertDoesNotExist()

            // Leaving the screen drops FLAG_SECURE, chat is normal again.
            fakeGate.resolveSuccess()
            compose.waitForIdle()
            compose.onNodeWithTag("voice_back").performClick()
            compose.waitForIdle()
            assertEquals(false, windowSecure())
        }
    }
}
