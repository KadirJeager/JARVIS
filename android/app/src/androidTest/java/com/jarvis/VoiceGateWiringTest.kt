package com.jarvis

import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
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
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.ConfirmResponse
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
            apis = ApiSet(FakeChatApi(), FakeVoiceApi()),
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
}
