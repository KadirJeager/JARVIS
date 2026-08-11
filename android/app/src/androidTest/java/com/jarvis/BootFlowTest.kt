package com.jarvis

import android.content.Context
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.auth.AuthClient
import com.jarvis.data.auth.AuthStateStore
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
import com.jarvis.data.net.DeviceTokenApi
import com.jarvis.data.net.DeviceTokenRequest
import com.jarvis.data.net.DeviceTokenResponse
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
import kotlinx.coroutines.CompletableDeferred
import org.junit.After
import org.junit.Assert.assertTrue
import androidx.test.rule.GrantPermissionRule
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Pins the boot path in the REAL [MainActivity]: a device that has signed in before must
 * land in the chat immediately, without waiting on Credential Manager.
 *
 * This is the defect Kadir hit in the field — "oturum açılıyor" spinning on every single
 * launch. The cause was that the Activity awaited `silentSignIn()` before drawing
 * anything, and Credential Manager takes its time. [com.jarvis.ui.chat.ChatViewModelTest]
 * covers what `onReturningUser()` DOES, but nothing there would notice if the Activity
 * stopped calling it — the same "guard tested, production never calls it" shape this
 * repo has shipped four times. Hence a test at the Activity, with a silent sign-in that
 * deliberately never completes.
 */
@RunWith(AndroidJUnit4::class)
class BootFlowTest {

    @get:Rule val compose = createEmptyComposeRule()

    // POST_NOTIFICATIONS is now requested at STARTUP (approvals reach Kadir by push,
    // North Star §4.8), not only inside startVoice(). Without this grant the system
    // dialog opens over every MainActivity launch here and deadlocks the whole class.
    @get:Rule
    val notificationPermission: GrantPermissionRule =
        GrantPermissionRule.grant(android.Manifest.permission.POST_NOTIFICATIONS)


    /**
     * Silent sign-in that HANGS. That is the whole point: if the boot path awaits it
     * before drawing, the chat never appears and this test fails. Real Credential Manager
     * does not hang forever, it is merely slow — this makes "slow" deterministic.
     */
    private class HangingAuthClient : AuthClient {
        private val never = CompletableDeferred<String>()
        override fun currentToken(): String? = null
        override suspend fun signIn(activityContext: Context): Result<String> =
            Result.success(never.await())
        override suspend fun silentSignIn(force: Boolean): Result<String> = Result.success(never.await())
    }

    private class AlreadySignedIn : AuthStateStore {
        override suspend fun hasSignedInBefore() = true
        override suspend fun markSignedIn() = Unit
        override suspend fun clearSignedIn() = Unit
    }

    private class NeverSignedIn : AuthStateStore {
        var cleared = false
        override suspend fun hasSignedInBefore() = false
        override suspend fun markSignedIn() = Unit
        override suspend fun clearSignedIn() { cleared = true }
    }

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
     * Watch pairing is wired into [AppContainer] like every other API, so this test has to
     * supply one. It answers locally rather than reaching the deployed backend: a test
     * suite must not talk to production at all.
     */
    private class FakeDeviceTokenApi : DeviceTokenApi {
        override suspend fun mint(req: DeviceTokenRequest) =
            DeviceTokenResponse("jdt_fake", "id", req.device, "2099-01-01T00:00:00Z")
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

    @After
    fun tearDown() {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        app.container = AppContainer(app)
    }

    private fun launchWith(state: AuthStateStore) {
        val app = ApplicationProvider.getApplicationContext<JarvisApp>()
        app.container = AppContainer(
            app,
            authManager = HangingAuthClient(),
            apis = ApiSet(FakeChatApi(), FakeVoiceApi(), FakeConversationsApi(), FakeApprovalApi(), FakeFcmApi(), FakeDeviceTokenApi()),
            authStateStore = state,
        )
    }

    @Test
    fun aReturningUser_seesTheChat_evenWhileSilentSignInIsStillPending() {
        launchWith(AlreadySignedIn())

        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()

            // The chat's own input bar — reachable only from AuthPhase.SIGNED_IN.
            compose.onNodeWithTag("chat_input").assertIsDisplayed()
            // And no boot splash, which is what was spinning on every launch.
            compose.onNodeWithTag("boot_splash").assertDoesNotExist()
        }
    }

    /**
     * The first-ever launch is the case CHECKING exists for: we genuinely do not know
     * who this is, so the splash is correct and the chat must NOT be shown.
     */
    @Test
    fun aFirstEverLaunch_stillWaitsOnTheSplash() {
        launchWith(NeverSignedIn())

        ActivityScenario.launch(MainActivity::class.java).use {
            compose.waitForIdle()

            compose.onNodeWithTag("boot_splash").assertIsDisplayed()
            compose.onNodeWithTag("chat_input").assertDoesNotExist()
        }
    }
}
