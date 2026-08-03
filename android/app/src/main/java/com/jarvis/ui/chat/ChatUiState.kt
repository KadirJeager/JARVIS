package com.jarvis.ui.chat

import com.jarvis.data.approvals.Approval
import com.jarvis.data.chat.Conversation
import com.jarvis.data.chat.UiMessage

/**
 * Where the session stands. This is four states, not one boolean: the app boots into
 * [CHECKING] while silent re-auth is in flight, and the sign-in screen must NOT be
 * shown then. Collapsing that into `signedIn = false` made every warm start flash
 * "Google ile giriş" at an already-authorized user before flipping to the chat.
 */
enum class AuthPhase {
    /** Silent re-auth is in flight. Show a quiet splash — never the sign-in CTA. */
    CHECKING,

    /** No usable credential. Show the sign-in screen. */
    SIGNED_OUT,

    /** The interactive credential flow is running; the CTA shows progress and is inert. */
    SIGNING_IN,

    /** Authenticated; the chat thread is live. */
    SIGNED_IN,
}

/** Immutable UI state for the chat screen. */
data class ChatUiState(
    val messages: List<UiMessage> = emptyList(),
    val input: String = "",
    val sending: Boolean = false,
    val loading: Boolean = false,
    val error: String? = null,
    val authPhase: AuthPhase = AuthPhase.CHECKING,
    /** Previous conversations, newest first. Loaded when the list is opened. */
    val conversations: List<Conversation> = emptyList(),
    val conversationsOpen: Boolean = false,
    val conversationsLoading: Boolean = false,

    /**
     * Known approvals by id, as the SERVER last described them (spec §7).
     *
     * Deliberately keyed rather than merged into [messages]: a transcript row is an
     * append-only record that a card was created, and the card's verdict changes after
     * that row is written — from another device, or from the clock running out. Storing
     * the badge in the row would freeze a stale answer into the history forever.
     */
    val approvals: Map<String, Approval> = emptyMap(),

    /** Approvals with a decision in flight. Their buttons are inert AND look inert. */
    val decidingApprovals: Set<String> = emptySet(),

    /** The approval a notification asked us to show (`data.approval_id`, spec §10). */
    val focusedApprovalId: String? = null,
) {
    /** Derived, never stored: one source of truth for "is the session live". */
    val signedIn: Boolean get() = authPhase == AuthPhase.SIGNED_IN

    /** Approval ids the thread already draws as cards. */
    private val threadApprovalIds: Set<String>
        get() = messages.mapNotNull { it.approvalId }.toSet()

    /**
     * Approvals the thread does NOT show, pinned below it.
     *
     * Pending ones are here because that is what a missed push looks like after a queue
     * sync (spec §4.3): the approval exists, the transcript row never reached this device
     * or belongs to another conversation, and it must still be decidable.
     *
     * [focusedApprovalId] is here for a second reason. A notification can point at an
     * approval that has since been decided or expired, and its transcript row may live in
     * a conversation Kadir is not reading. "Tapping the push shows that card" (spec §10)
     * has to survive that, and a card showing "Süresi doldu" is a real answer where a
     * blank screen is not.
     *
     * Newest first, matching the queue.
     */
    val pinnedApprovals: List<Approval>
        get() = approvals.values
            .filter { (it.decidable || it.id == focusedApprovalId) && it.id !in threadApprovalIds }
            .sortedByDescending { it.createdAt.orEmpty() }

    fun isDeciding(approvalId: String): Boolean = approvalId in decidingApprovals

    /**
     * Whether the decision buttons of [approvalId] are live.
     *
     * ONE flag, read by the card for both `enabled` and its appearance. That is the point:
     * a build where the buttons are inert but still look pressable cannot exist when there
     * is only one source for both (the 10296e1 lesson — a disabled action must LOOK
     * disabled). It is false for an unknown id, for anything already decided, and for the
     * whole time a decision is in flight.
     */
    fun canDecide(approvalId: String): Boolean =
        approvals[approvalId]?.decidable == true && !isDeciding(approvalId)
}
