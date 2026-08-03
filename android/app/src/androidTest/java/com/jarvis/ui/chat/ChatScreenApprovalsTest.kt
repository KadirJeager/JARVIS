package com.jarvis.ui.chat

import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.jarvis.data.approvals.Approval
import com.jarvis.data.approvals.ApprovalStatus
import com.jarvis.data.chat.KIND_APPROVAL
import com.jarvis.data.chat.UiMessage
import com.jarvis.ui.theme.JarvisTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/**
 * NOT RUN in this slice (the device was busy). These are the claims about the THREAD that
 * the ViewModel suite cannot make: which rows become cards, which stay bubbles, and
 * whether a card the transcript never mentioned still reaches the screen.
 */
class ChatScreenApprovalsTest {

    @get:Rule
    val rule = createComposeRule()

    private fun approval(id: String, status: ApprovalStatus = ApprovalStatus.PENDING) = Approval(
        id = id,
        title = "'cancel_reminder' çalıştırılsın mı?",
        detail = "Kırmızı bölge eylemi.",
        status = status,
        createdAt = "2026-08-03T10:00:00+00:00",
    )

    private fun setScreen(
        state: ChatUiState,
        onApprove: (String) -> Unit = {},
        onReject: (String) -> Unit = {},
    ) = rule.setContent {
        JarvisTheme {
            ChatScreen(
                state = state.copy(authPhase = AuthPhase.SIGNED_IN),
                onInput = {},
                onSend = {},
                onRetry = {},
                onApproveApproval = onApprove,
                onRejectApproval = onReject,
            )
        }
    }

    /** Spec §4.8/§7: the card rides the thread — it is not a separate screen. */
    @Test
    fun anApprovalRowInTheThread_drawsAsACard_notABubble() {
        setScreen(
            ChatUiState(
                messages = listOf(
                    UiMessage("user", "o hatırlatmayı sil"),
                    UiMessage("model", "🔔 Onay bekliyor", KIND_APPROVAL, "a1"),
                ),
                approvals = mapOf("a1" to approval("a1")),
            ),
        )

        rule.onNodeWithText("o hatırlatmayı sil").assertIsDisplayed()
        rule.onNodeWithTag("approval_card_a1").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertIsDisplayed()
    }

    /**
     * The tolerant-wire boundary, on screen: an `approval` row whose document has not
     * arrived (or a `kind` from a newer backend) degrades to the plain bubble the server's
     * own Turkish text already fills.
     */
    @Test
    fun anApprovalRowWithNoLoadedDocument_staysAPlainBubble() {
        setScreen(
            ChatUiState(
                messages = listOf(UiMessage("model", "🔔 Onay bekliyor", KIND_APPROVAL, "a1")),
                approvals = emptyMap(),
            ),
        )

        rule.onNodeWithText("🔔 Onay bekliyor").assertIsDisplayed()
        rule.onNodeWithTag("approval_card_a1").assertDoesNotExist()
    }

    @Test
    fun anUnknownKind_staysAPlainBubble() {
        setScreen(
            ChatUiState(
                messages = listOf(UiMessage("model", "gelecekten bir satır", "hologram", null)),
            ),
        )

        rule.onNodeWithText("gelecekten bir satır").assertIsDisplayed()
    }

    /**
     * Spec §4.3: the push was lost, the thread knows nothing about this approval, and it
     * must STILL be visible and decidable after a queue sync.
     */
    @Test
    fun aPendingApprovalTheThreadNeverMentioned_isPinnedBelowIt() {
        setScreen(
            ChatUiState(
                messages = listOf(UiMessage("user", "selam")),
                approvals = mapOf("a9" to approval("a9")),
            ),
        )

        rule.onNodeWithTag("approval_card_a9").assertIsDisplayed()
    }

    /** The same approval must not appear twice — once in the thread, once pinned. */
    @Test
    fun anApprovalDrawnInTheThread_isNotPinnedAsWell() {
        val state = ChatUiState(
            messages = listOf(UiMessage("model", "🔔 Onay bekliyor", KIND_APPROVAL, "a1")),
            approvals = mapOf("a1" to approval("a1")),
        )
        assertEquals(0, state.pinnedApprovals.size)

        setScreen(state)
        rule.onAllNodesWithTag("approval_card_a1").assertCountEquals(1)
    }

    @Test
    fun decisionsFromTheThread_reachTheCallbacksWithTheApprovalId() {
        var approved: String? = null
        var rejected: String? = null
        setScreen(
            ChatUiState(
                messages = listOf(UiMessage("model", "🔔", KIND_APPROVAL, "a1")),
                approvals = mapOf("a1" to approval("a1")),
            ),
            onApprove = { approved = it },
            onReject = { rejected = it },
        )

        rule.onNodeWithTag("approve_a1").performClick()
        rule.onNodeWithTag("reject_a1").performClick()

        assertEquals("a1", approved)
        assertEquals("a1", rejected)
    }

    /**
     * An empty thread with a pending approval is NOT the empty state: hiding a red action
     * behind "Bir şey sor, başlayalım." is exactly the failure §4.3 exists to prevent.
     */
    @Test
    fun aPinnedApprovalSuppressesTheEmptyHint() {
        setScreen(ChatUiState(approvals = mapOf("a9" to approval("a9"))))

        rule.onNodeWithTag("approval_card_a9").assertIsDisplayed()
        rule.onNodeWithText("Bir şey sor, başlayalım.").assertDoesNotExist()
    }

    /** A decided card stays in the thread as history, with no way to decide it again. */
    @Test
    fun aDecidedThreadCard_keepsItsPlaceWithoutButtons() {
        setScreen(
            ChatUiState(
                messages = listOf(UiMessage("model", "🔔", KIND_APPROVAL, "a1")),
                approvals = mapOf("a1" to approval("a1", ApprovalStatus.REJECTED)),
            ),
        )

        rule.onNodeWithTag("approval_card_a1").assertIsDisplayed()
        rule.onNodeWithText("Reddedildi").assertIsDisplayed()
        rule.onNodeWithTag("approve_a1").assertDoesNotExist()
    }
}
