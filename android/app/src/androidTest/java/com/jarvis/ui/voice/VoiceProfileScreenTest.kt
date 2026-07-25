package com.jarvis.ui.voice

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.voice.Correction
import com.jarvis.data.voice.HistoryRow
import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceProfile
import com.jarvis.data.voice.VoiceQuality
import com.jarvis.data.voice.VoiceSample
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class VoiceProfileScreenTest {

    @get:Rule val compose = createComposeRule()

    private val profile = VoiceProfile(
        counts = VoiceCounts(anchors = 2, auto = 1, manual = 0),
        samples = listOf(
            VoiceSample("s1", SampleSource.ENROLL, "2026-07-25T00:00:00Z", "buds", "kulaklik", null),
            VoiceSample("s2", SampleSource.AUTO, "2026-07-25T01:00:00Z", "arctis", null, null),
        ),
        history = listOf(
            HistoryRow("h1", "2026-07-25T02:00:00Z", 0.71, true, "buds", "HIGH", "s2", Correction.NONE),
        ),
        quality = VoiceQuality(0.71, 0.1, mapOf("buds" to 0.7), emptyMap(), 0.72, 0.65),
    )

    private fun ready() = VoiceProfileUiState(
        gate = GatePhase.UNLOCKED,
        profile = profile,
        summary = summarize(profile.quality, profile.counts),
    )

    private fun render(
        state: VoiceProfileUiState,
        onSetLabel: (String, String?) -> Unit = { _, _ -> },
        onDeleteSample: (String) -> Unit = {},
    ) {
        compose.setContent {
            VoiceProfileScreen(
                state = state,
                onBack = {},
                onRetryUnlock = {},
                onRetryLoad = {},
                onSetLabel = onSetLabel,
                onDeleteSample = onDeleteSample,
                onConfirm = {},
                onReject = {},
                onDeleteProfile = {},
                onDismissError = {},
            )
        }
    }

    /** Nothing about the voice profile may render before the device lock is satisfied. */
    @Test
    fun whileChecking_showsTheLockedState_andNoProfileContent() {
        render(VoiceProfileUiState(gate = GatePhase.CHECKING))
        compose.onNodeWithTag("voice_locked").assertIsDisplayed()
        compose.onAllNodesWithTag("voice_sample_s1").assertCountEquals(0)
    }

    @Test
    fun whenDenied_offersARetry() {
        render(VoiceProfileUiState(gate = GatePhase.DENIED, error = "Kilit açılamadı: iptal"))
        compose.onNodeWithTag("voice_retry_unlock").assertIsDisplayed()
        compose.onNodeWithText("Kilit açılamadı: iptal").assertIsDisplayed()
    }

    @Test
    fun whenUnlocked_showsTheStatusHeadlineAndDetail() {
        render(ready())
        compose.onNodeWithTag("voice_status_headline").assertIsDisplayed()
        compose.onNodeWithText("Tanınma güçlü").assertIsDisplayed()
        compose.onNodeWithTag("voice_status_detail").assertIsDisplayed()
    }

    @Test
    fun galleryRowsShowTheirSourceBadgeAndDevice() {
        render(ready())
        compose.onNodeWithTag("voice_sample_s1").assertIsDisplayed()
        compose.onNodeWithText("kayıt").assertIsDisplayed()
        compose.onNodeWithText("otomatik").assertIsDisplayed()
        compose.onNodeWithText("buds").assertIsDisplayed()
    }

    @Test
    fun aLabelledSampleShowsItsTurkishLabel_notTheAsciiWireValue() {
        render(ready())
        compose.onNodeWithText("Kulaklık").assertIsDisplayed()
    }

    @Test
    fun choosingALabel_reportsTheAsciiWireValue_notTheDisplayName() {
        var chosen: Pair<String, String?>? = null
        render(ready(), onSetLabel = { id, label -> chosen = id to label })

        compose.onNodeWithTag("voice_sample_label_s2").performClick()
        compose.onNodeWithTag("voice_label_option_gurultulu").performClick()

        assertEquals("s2" to "gurultulu", chosen)
    }

    @Test
    fun deletingASample_reportsItsId() {
        var deleted: String? = null
        render(ready(), onDeleteSample = { deleted = it })
        compose.onNodeWithTag("voice_sample_delete_s2").performClick()
        assertEquals("s2", deleted)
    }

    /** A row already mutating must not accept a second tap (the VM would drop it anyway,
     *  but a live-looking button that does nothing reads as a broken screen). */
    @Test
    fun aRowBeingMutated_disablesItsActions() {
        render(ready().copy(mutatingId = "s2"))
        compose.onNodeWithTag("voice_sample_delete_s2").assertIsNotEnabled()
    }

    @Test
    fun anEmptyProfile_showsTheEmptyStateInsteadOfABlankScreen() {
        val empty = VoiceProfile(
            VoiceCounts(0, 0, 0), emptyList(), emptyList(),
            VoiceQuality(null, null, emptyMap(), emptyMap(), null, null),
        )
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED,
                profile = empty,
                summary = summarize(empty.quality, empty.counts),
            ),
        )
        compose.onNodeWithTag("voice_empty").assertIsDisplayed()
        compose.onNodeWithText("Ses kimliği yok").assertIsDisplayed()
    }

    /** Spec §9: the screen is built expecting 3b's actions from day one. */
    @Test
    fun the3bPlaceholdersArePresentButInert() {
        render(ready())
        compose.onNodeWithTag("voice_placeholder_record").assertIsNotEnabled()
        compose.onNodeWithTag("voice_placeholder_retrain").assertIsNotEnabled()
    }

    @Test
    fun anErrorIsShownWithTheServersWording() {
        render(ready().copy(error = "Son çapa silinemez: çapasız profil ses doğrulayamaz."))
        compose.onNodeWithTag("voice_error").assertIsDisplayed()
        compose.onNodeWithText("Son çapa silinemez: çapasız profil ses doğrulayamaz.")
            .assertIsDisplayed()
    }
}
