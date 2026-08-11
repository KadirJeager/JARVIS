package com.jarvis.ui.voice

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollToNode
import androidx.compose.ui.test.performTextClearance
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.jarvis.data.voice.Correction
import com.jarvis.data.voice.HistoryRow
import com.jarvis.data.voice.SampleSource
import com.jarvis.data.voice.VoiceCounts
import com.jarvis.data.voice.VoiceProfile
import com.jarvis.data.voice.VoiceQuality
import com.jarvis.data.voice.VoiceSample
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Covers the simplified voice-identity screen (user feedback: "karışık, hata yapmaya
 * müsait, çok veri görünüyor"). The default view now shows only the status card and a
 * capped recent-history list; the full sample gallery, the 3b placeholders and the
 * danger zone moved behind a collapsed "Ses örneklerini yönet" section (tests that
 * target them now expand it first via [expandManageSection]). Both destructive
 * one-tap affordances (sample delete, history correction) now require an explicit
 * confirm step through a dialog.
 */
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
            // Deliberately a device NOT used by any sample above: the history row now
            // shows its device hint bare (no "Cihaz:" prefix, per the decluttered
            // design), so reusing "buds" here would make it ambiguous with sample s1's
            // device text once the manage section is expanded on the same screen.
            HistoryRow("h1", "2026-07-25T02:00:00Z", 0.71, true, "telefon", "HIGH", "s2", Correction.NONE),
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
        onConfirm: (String) -> Unit = {},
        onReject: (String) -> Unit = {},
        onDeleteProfile: () -> Unit = {},
    ) {
        compose.setContent {
            VoiceProfileScreen(
                state = state,
                onBack = {},
                onRetryUnlock = {},
                onRetryLoad = {},
                onSetLabel = onSetLabel,
                onDeleteSample = onDeleteSample,
                onConfirm = onConfirm,
                onReject = onReject,
                onDeleteProfile = onDeleteProfile,
                onDismissError = {},
                // Task 9's enrollment flow has its own ViewModel; this screen test is
                // not about it, so it renders idle and inert.
                enrollState = EnrollState.Idle,
                onStartEnrollDevice = {},
                onProceedToRecording = {},
            )
        }
    }

    /** The sample gallery, placeholders and danger zone now live behind this toggle,
     *  collapsed by default (see [manageSectionIsCollapsedByDefault]). Tests that need
     *  them expand it first. */
    private fun expandManageSection() {
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_manage_toggle"))
        compose.onNodeWithTag("voice_manage_toggle").performClick()
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

    /** Default-view declutter (user feedback: "çok veri görünüyor"): the raw score/
     *  percentage breakdown must NOT appear on the default view; the status card shows
     *  a plain sentence instead. */
    @Test
    fun theStatusCardShowsAPlainSentence_notTheRawScoreBreakdown() {
        render(ready())
        compose.onNodeWithText("Sesini büyük çoğunlukla doğru tanıyor.").assertIsDisplayed()
        compose.onAllNodesWithText("Doğrulanmış ortalama 0.71", substring = true)
            .assertCountEquals(0)
    }

    /** Hard requirement: the sample gallery, placeholders and danger zone are collapsed
     *  by default — not merely styled to look collapsed, actually absent from the tree
     *  until the user opts in. */
    @Test
    fun manageSectionIsCollapsedByDefault() {
        render(ready())
        compose.onAllNodesWithTag("voice_sample_s1").assertCountEquals(0)
        compose.onAllNodesWithTag("voice_placeholder_record").assertCountEquals(0)
        compose.onAllNodesWithTag("voice_danger_open").assertCountEquals(0)

        expandManageSection()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_s1"))
        compose.onNodeWithTag("voice_sample_s1").assertIsDisplayed()
    }

    @Test
    fun galleryRowsShowTheirSourceBadgeAndDevice() {
        render(ready())
        expandManageSection()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_s1"))
        compose.onNodeWithTag("voice_sample_s1").assertIsDisplayed()
        compose.onNodeWithText("kayıt").assertIsDisplayed()
        compose.onNodeWithText("otomatik").assertIsDisplayed()
        compose.onNodeWithText("buds").assertIsDisplayed()
    }

    @Test
    fun aLabelledSampleShowsItsTurkishLabel_notTheAsciiWireValue() {
        render(ready())
        expandManageSection()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_s1"))
        compose.onNodeWithText("Kulaklık").assertIsDisplayed()
    }

    @Test
    fun choosingALabel_reportsTheAsciiWireValue_notTheDisplayName() {
        var chosen: Pair<String, String?>? = null
        render(ready(), onSetLabel = { id, label -> chosen = id to label })
        expandManageSection()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_label_s2"))
        compose.onNodeWithTag("voice_sample_label_s2").performClick()
        compose.onNodeWithTag("voice_label_option_gurultulu").performClick()

        assertEquals("s2" to "gurultulu", chosen)
    }

    /**
     * Sample deletion used to fire on a single tap. It now requires an explicit
     * confirm: tapping "Sil" alone must NOT call back — only tapping the dialog's
     * "Evet, sil" does. This is deliberately a plain confirm, not the typed "SIL"
     * gate (that stays reserved for whole-profile deletion).
     */
    @Test
    fun deletingASample_requiresConfirmation_thenReportsItsId() {
        var deleted: String? = null
        render(ready(), onDeleteSample = { deleted = it })
        expandManageSection()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_delete_s2"))
        compose.onNodeWithTag("voice_sample_delete_s2").performClick()
        assertEquals(null, deleted) // tapping "Sil" alone must not delete anything yet

        compose.onNodeWithTag("voice_sample_delete_confirm_s2").assertIsDisplayed()
        compose.onNodeWithTag("voice_sample_delete_confirm_s2").performClick()

        assertEquals("s2", deleted)
    }

    /** The confirm dialog's "Vazgeç" must close it without ever calling back. */
    @Test
    fun cancellingTheSampleDeleteDialog_reportsNothing() {
        var deleted: String? = null
        render(ready(), onDeleteSample = { deleted = it })
        expandManageSection()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_delete_s2"))
        compose.onNodeWithTag("voice_sample_delete_s2").performClick()
        compose.onNodeWithTag("voice_sample_delete_cancel_s2").performClick()

        compose.onNodeWithTag("voice_sample_delete_dialog_s2").assertDoesNotExist()
        assertEquals(null, deleted)
    }

    /** The lock is screen-wide, not per-row: [VoiceProfileViewModel.mutate] starts with
     *  `if (_state.value.mutatingId != null) return`, so only ONE mutation can be in
     *  flight for the whole screen regardless of which row it targets. If a row NOT
     *  being mutated stayed enabled, tapping it would silently drop the click — the
     *  exact "live-looking button that does nothing" this test exists to catch. So this
     *  asserts both the mutating row (s2) AND an unrelated row (s1) are disabled; the s1
     *  assertion is the one that discriminates, because under a (wrong) per-row lock
     *  `busy(s1) = (s1 == "s2") = false`, so s1 would stay enabled and that assertion
     *  would fail. */
    @Test
    fun aRowBeingMutated_disablesItsActions() {
        render(ready().copy(mutatingId = "s2"))
        expandManageSection()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_sample_delete_s2"))
        compose.onNodeWithTag("voice_sample_delete_s2").assertIsNotEnabled()
        compose.onNodeWithTag("voice_sample_delete_s1").assertIsNotEnabled()
        compose.onNodeWithTag("voice_sample_label_s1").assertIsNotEnabled()
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

    /** Spec §9: the screen is built expecting 3b's actions from day one. They now live
     *  in the collapsed "Ses örneklerini yönet" section. */
    @Test
    fun the3bPlaceholdersArePresentButInert() {
        render(ready())
        expandManageSection()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_placeholder_record"))
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

    /** The row itself now shows the plain verdict, device and date only — the numeric
     *  score moved into the detail dialog (spec: "the numeric score belongs in the
     *  detail, not the list"). */
    @Test
    fun historyRowsShowVerdictDeviceAndDate_butNotTheRawScore() {
        render(ready())
        compose.onNodeWithTag("voice_history_h1").assertIsDisplayed()
        compose.onNodeWithText("tanındı").assertIsDisplayed()
        compose.onAllNodesWithText("0.71").assertCountEquals(0)
    }

    /** Tapping the row is the "detail": that's where the numeric score now lives. */
    @Test
    fun tappingAHistoryRow_opensADialogShowingTheScore() {
        render(ready())
        compose.onNodeWithTag("voice_history_h1").performClick()
        compose.onNodeWithTag("voice_history_dialog_h1").assertIsDisplayed()
        compose.onNodeWithText("0.71", substring = true).assertIsDisplayed()
    }

    /**
     * Replaces the old always-live per-row "Bendim"/"Ben değildim" pair — a single
     * mis-tap in a dense scrolling list used to delete the gallery sample that
     * utterance became. Now the row must be tapped to open a dialog before either
     * action is reachable, and the dialog closes on each decision (spec §6: reversal
     * in BOTH directions stays legal, proven here by reopening the dialog for the
     * second action).
     */
    @Test
    fun historyRowDialog_offersBothCorrections_oneDecisionPerOpen() {
        var confirmed: String? = null
        var rejected: String? = null
        render(ready(), onConfirm = { confirmed = it }, onReject = { rejected = it })

        // Not reachable before the row is tapped.
        compose.onAllNodesWithTag("voice_confirm_h1").assertCountEquals(0)

        compose.onNodeWithTag("voice_history_h1").performClick()
        compose.onNodeWithTag("voice_confirm_h1").performClick()
        assertEquals("h1", confirmed)
        compose.onNodeWithTag("voice_history_dialog_h1").assertDoesNotExist() // closes on decision

        compose.onNodeWithTag("voice_history_h1").performClick()
        compose.onNodeWithTag("voice_reject_h1").performClick()
        assertEquals("h1", rejected)
    }

    /** An already-ruled row must show its verdict and stop offering the same action —
     *  the endpoint is idempotent, but a live button that changes nothing reads broken. */
    @Test
    fun anAlreadyConfirmedRow_showsItsVerdict_andItsDialogDoesNotOfferConfirmAgain() {
        val ruled = profile.copy(
            history = listOf(
                HistoryRow("h2", "2026-07-25T03:00:00Z", 0.8, true, "buds", "HIGH", "s3",
                    Correction.CONFIRMED),
            ),
        )
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED, profile = ruled,
                summary = summarize(ruled.quality, ruled.counts),
            ),
        )
        compose.onNodeWithText("bendim").assertIsDisplayed()

        compose.onNodeWithTag("voice_history_h2").performClick()
        compose.onNodeWithTag("voice_confirm_h2").assertIsNotEnabled()
        compose.onNodeWithTag("voice_reject_h2").assertIsEnabled()   // reversal stays legal
    }

    /** Mirrors [anAlreadyConfirmedRow_showsItsVerdict_andItsDialogDoesNotOfferConfirmAgain]:
     *  spec §6 guarantees reversal in BOTH directions, not just away from CONFIRMED. */
    @Test
    fun anAlreadyRejectedRow_showsItsVerdict_andItsDialogDoesNotOfferRejectAgain() {
        val ruled = profile.copy(
            history = listOf(
                HistoryRow("h3", "2026-07-25T03:00:00Z", 0.8, true, "buds", "HIGH", "s3",
                    Correction.REJECTED),
            ),
        )
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED, profile = ruled,
                summary = summarize(ruled.quality, ruled.counts),
            ),
        )
        compose.onNodeWithText("ben değildim").assertIsDisplayed()

        compose.onNodeWithTag("voice_history_h3").performClick()
        compose.onNodeWithTag("voice_reject_h3").assertIsNotEnabled()
        compose.onNodeWithTag("voice_confirm_h3").assertIsEnabled()   // reversal stays legal
    }

    /** Only the most recent handful of history rows show by default; "Daha fazla
     *  göster" reveals the rest. */
    @Test
    fun recentHistoryIsCappedByDefault_withADahaFazlaToExpandIt() {
        val manyRows = (1..7).map { i ->
            HistoryRow("h$i", "2026-07-25T0$i:00:00Z", 0.7, true, "buds", "HIGH", null, Correction.NONE)
        }
        val big = profile.copy(history = manyRows)
        render(
            VoiceProfileUiState(
                gate = GatePhase.UNLOCKED, profile = big,
                summary = summarize(big.quality, big.counts),
            ),
        )

        compose.onNodeWithTag("voice_history_h1").assertIsDisplayed()
        compose.onAllNodesWithTag("voice_history_h6").assertCountEquals(0)
        compose.onAllNodesWithTag("voice_history_h7").assertCountEquals(0)

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_history_show_more"))
        compose.onNodeWithTag("voice_history_show_more").performClick()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_history_h7"))
        compose.onNodeWithTag("voice_history_h7").assertIsDisplayed()
    }

    /**
     * Profile deletion is irreversible and takes the history with it, so it must not be
     * one stray tap away. The danger zone now lives inside the collapsed "Ses
     * örneklerini yönet" section, so reaching it needs expanding that section first,
     * then performScrollToNode — a bare performScrollTo() won't do: an un-composed
     * lazy item isn't in the semantics tree yet.
     */
    @Test
    fun profileDeletion_requiresTypingTheConfirmationWord() {
        var deleted = false
        render(ready(), onDeleteProfile = { deleted = true })
        expandManageSection()

        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_danger_open"))
        compose.onNodeWithTag("voice_danger_open").performClick()
        compose.onNodeWithTag("voice_list").performScrollToNode(hasTestTag("voice_danger_confirm"))
        compose.onNodeWithTag("voice_danger_confirm").assertIsNotEnabled()

        compose.onNodeWithTag("voice_danger_input").performTextInput("sil")
        compose.onNodeWithTag("voice_danger_confirm").assertIsNotEnabled()   // wrong case

        compose.onNodeWithTag("voice_danger_input").performTextClearance()
        compose.onNodeWithTag("voice_danger_input").performTextInput(DELETE_CONFIRM_WORD)
        compose.onNodeWithTag("voice_danger_confirm").assertIsEnabled()
        compose.onNodeWithTag("voice_danger_confirm").performClick()
        assertTrue(deleted)
    }

    /**
     * Turkish case folding is locale-dependent (I<->ı, İ<->i): a dotted "SİL" lowercased
     * on a Turkish-locale device produces a combining sequence that silently never
     * matches, so the user types the right word and the button never unlocks. The
     * confirmation word is ASCII and compared with no case transformation at all.
     */
    @Test
    fun theConfirmationWordIsAsciiSoTurkishCaseFoldingCannotBreakIt() {
        assertEquals("SIL", DELETE_CONFIRM_WORD)
        assertTrue(DELETE_CONFIRM_WORD.all { it.code < 128 })
    }
}
