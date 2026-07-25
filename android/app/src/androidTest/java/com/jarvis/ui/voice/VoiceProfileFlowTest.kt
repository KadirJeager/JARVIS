package com.jarvis.ui.voice

import androidx.compose.runtime.collectAsState
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.net.ConfirmResponse
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
import com.jarvis.ui.Route
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Drives the REAL ViewModel + repository against a fake backend, through the real
 * screen — the biometric prompt itself is the only thing stubbed, because a system
 * prompt cannot be driven from an instrumented test on a lockless emulator.
 */
@RunWith(AndroidJUnit4::class)
class VoiceProfileFlowTest {

    @get:Rule val compose = createComposeRule()

    private class FakeVoiceApi : VoiceApi {
        var confirmed: String? = null
        var profileCalls = 0
        override suspend fun profile(): VoiceProfileResponse {
            profileCalls++
            return VoiceProfileResponse(
                counts = VoiceCountsDto(anchors = 1, auto = 1, manual = 0),
                samples = listOf(
                    VoiceSampleDto("s1", "enroll", "2026-07-25T00:00:00Z", "buds", "kulaklik"),
                ),
                history = listOf(VoiceHistoryDto("h1", "2026-07-25T01:00:00Z", 0.71, true, "buds")),
            )
        }
        override suspend fun patchLabel(id: String, req: LabelPatch) =
            VoiceSampleDto(id, "auto", label = req.label)
        override suspend fun patchNote(id: String, req: NotePatch) =
            VoiceSampleDto(id, "auto", note = req.note)
        override suspend fun deleteSample(id: String) = SampleDeletedResponse(id)
        override suspend fun confirm(id: String): ConfirmResponse {
            confirmed = id
            return ConfirmResponse("s-new")
        }
        override suspend fun reject(id: String) = RejectResponse(null)
        override suspend fun deleteProfile() = ProfileDeletedResponse(true)
    }

    @Test
    fun unlockingLoadsTheProfile_andACorrectionReachesTheBackendThenReloads() {
        val api = FakeVoiceApi()
        val vm = VoiceProfileViewModel(VoiceProfileRepository(api))

        compose.setContent {
            val state = vm.state.collectAsState().value
            VoiceProfileScreen(
                state = state,
                onBack = {}, onRetryUnlock = { vm.onUnlocked() }, onRetryLoad = { vm.load() },
                onSetLabel = vm::setLabel, onDeleteSample = vm::deleteSample,
                onConfirm = vm::confirm, onReject = vm::reject,
                onDeleteProfile = vm::deleteProfile, onDismissError = vm::dismissError,
            )
        }

        // Gate closed: nothing fetched, nothing shown.
        compose.onNodeWithTag("voice_locked").assertIsDisplayed()
        assertEquals(0, api.profileCalls)

        vm.onUnlocked()
        compose.waitForIdle()

        // "Son söyleyişler" is default-view content; the sample gallery (where "kayıt"
        // and "Kulaklık" live) moved behind the collapsed "Ses örneklerini yönet"
        // section as part of the screen's simplification, so it must be expanded first.
        compose.onNodeWithTag("voice_history_h1").assertIsDisplayed()
        assertEquals(1, api.profileCalls)

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_manage_toggle"))
        compose.onNodeWithTag("voice_manage_toggle").performClick()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_s1"))
        compose.onNodeWithText("kayıt").assertIsDisplayed()
        compose.onNodeWithText("Kulaklık").assertIsDisplayed()

        // The history row's "Bendim"/"Ben değildim" now live behind a confirm dialog
        // opened by tapping the row, rather than firing on a single row-level tap.
        compose.onNodeWithTag("voice_history_h1").performClick()
        compose.onNodeWithTag("voice_confirm_h1").performClick()
        compose.waitForIdle()

        assertEquals("h1", api.confirmed)
        assertEquals("düzeltme sonrası sunucudan yeniden okunmalı", 2, api.profileCalls)
    }

    @Test
    fun theChatScreenOffersAWayIntoTheVoiceProfile() {
        var route = Route.CHAT
        compose.setContent {
            com.jarvis.ui.chat.ChatScreen(
                state = com.jarvis.ui.chat.ChatUiState(
                    authPhase = com.jarvis.ui.chat.AuthPhase.SIGNED_IN,
                ),
                onInput = {}, onSend = {}, onRetry = {},
                onOpenVoiceProfile = { route = Route.VOICE_PROFILE },
            )
        }
        compose.onNodeWithTag("open_voice_profile").performClick()
        assertEquals(Route.VOICE_PROFILE, route)
    }
}
