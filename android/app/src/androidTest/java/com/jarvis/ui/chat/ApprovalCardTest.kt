package com.jarvis.ui.chat

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.jarvis.data.approvals.Approval
import com.jarvis.data.approvals.ApprovalStatus
import com.jarvis.ui.theme.JarvisTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Rule
import org.junit.Test

/**
 * NOT RUN in this slice (the device was busy). Written now because the claims below are
 * exactly the ones a JVM test cannot make: the ViewModel suite pins
 * [ChatUiState.canDecide], but only a composed tree can prove the card actually READS it.
 *
 * The 10296e1 lesson lives here: a disabled action must LOOK disabled. `assertIsNotEnabled`
 * alone would pass for a button that is inert but rendered identically to a live one, so
 * the deciding case also asserts the buttons are GONE and replaced by a visible progress
 * row — the strongest available "not tappable right now".
 */
class ApprovalCardTest {

    @get:Rule
    val rule = createComposeRule()

    private fun approval(
        id: String = "a1",
        status: ApprovalStatus = ApprovalStatus.PENDING,
        outcome: String? = null,
    ) = Approval(
        id = id,
        title = "'cancel_reminder' çalıştırılsın mı?",
        detail = "Kırmızı bölge eylemi: cancel_reminder(reminder_id=r7).",
        status = status,
        toolName = "cancel_reminder",
        outcome = outcome,
    )

    private fun setCard(
        approval: Approval,
        canDecide: Boolean = true,
        deciding: Boolean = false,
        onApprove: () -> Unit = {},
        onReject: () -> Unit = {},
    ) = rule.setContent {
        JarvisTheme {
            ApprovalCard(
                approval = approval,
                canDecide = canDecide,
                deciding = deciding,
                onApprove = onApprove,
                onReject = onReject,
            )
        }
    }

    @Test
    fun pendingCard_showsTitleDetailBadgeAndBothButtons() {
        setCard(approval())

        rule.onNodeWithText("Onay bekliyor").assertIsDisplayed()
        rule.onNodeWithText("'cancel_reminder' çalıştırılsın mı?").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertIsDisplayed().assertIsEnabled()
        rule.onNodeWithTag("reject_a1").assertIsDisplayed().assertIsEnabled()
    }

    @Test
    fun tappingOnayla_andReddet_forwardTheirCallbacks() {
        var approved = 0
        var rejected = 0
        setCard(approval(), onApprove = { approved++ }, onReject = { rejected++ })

        rule.onNodeWithTag("approve_a1").performClick()
        rule.onNodeWithTag("reject_a1").performClick()

        assertEquals(1, approved)
        assertEquals(1, rejected)
    }

    /**
     * The 10296e1 pin. While the decision is in flight the buttons are not merely
     * disabled — they are replaced by a spinner and "Karar gönderiliyor…", so there is
     * nothing that looks pressable to press.
     */
    @Test
    fun whileDeciding_theButtonsAreGone_andTheCardSaysWhy() {
        setCard(approval(), canDecide = false, deciding = true)

        rule.onNodeWithTag("approval_deciding_a1").assertIsDisplayed()
        rule.onNodeWithText("Karar gönderiliyor…").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertDoesNotExist()
        rule.onNodeWithTag("reject_a1").assertDoesNotExist()
    }

    /**
     * A pending card whose buttons are off for a reason other than an in-flight decision
     * must still be visibly inert rather than silently ignoring taps.
     */
    @Test
    fun whenDecisionsAreDisabled_theButtonsAreShownDisabled_andSwallowTaps() {
        var tapped = false
        setCard(approval(), canDecide = false, deciding = false, onApprove = { tapped = true })

        rule.onNodeWithTag("approve_a1").assertIsNotEnabled()
        rule.onNodeWithTag("reject_a1").assertIsNotEnabled()
        rule.onNodeWithTag("approve_a1").performClick()
        assertFalse("kapalı düğme geri çağırma tetiklememeli", tapped)
    }

    /** A decided card is history: badge and outcome, no way to decide it again (§4.2). */
    @Test
    fun approvedCard_showsTheOutcome_andHasNoButtons() {
        setCard(
            approval(status = ApprovalStatus.APPROVED, outcome = "Hatırlatma iptal edildi."),
            canDecide = false,
        )

        rule.onNodeWithText("Onaylandı").assertIsDisplayed()
        rule.onNodeWithText("Hatırlatma iptal edildi.").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertDoesNotExist()
        rule.onNodeWithTag("reject_a1").assertDoesNotExist()
    }

    /** Spec §4.1: the clock decided, not Kadir — and it must not read as "Reddedildi". */
    @Test
    fun expiredCard_saysSuresiDoldu_andOffersNoDecision() {
        setCard(
            approval(status = ApprovalStatus.EXPIRED, outcome = "Onay süresi doldu; eylem çalıştırılmadı."),
            canDecide = false,
        )

        rule.onNodeWithText("Süresi doldu").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertDoesNotExist()
    }

    /** Spec §4.5: the tool blew up AFTER approval; the error is shown, not swallowed. */
    @Test
    fun failedCard_showsTheErrorObservation() {
        setCard(
            approval(status = ApprovalStatus.FAILED, outcome = "yürütme hatası: boom"),
            canDecide = false,
        )

        rule.onNodeWithText("Çalıştırılamadı").assertIsDisplayed()
        rule.onNodeWithTag("approval_outcome_a1").assertIsDisplayed()
    }

    /**
     * A status this build has never seen must NOT get decision buttons. Fail-closed: the
     * server may already have decided it.
     */
    @Test
    fun unknownStatus_offersNoDecision() {
        setCard(approval(status = ApprovalStatus.UNKNOWN), canDecide = false)

        rule.onNodeWithText("Durum bilinmiyor").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertDoesNotExist()
    }
}
