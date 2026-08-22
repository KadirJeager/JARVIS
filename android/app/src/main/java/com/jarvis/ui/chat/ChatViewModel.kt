package com.jarvis.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.approvals.Approval
import com.jarvis.data.approvals.ApprovalRepository
import com.jarvis.data.approvals.ApprovalStatus
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.ConversationsRepository
import com.jarvis.data.chat.UiMessage
import com.jarvis.data.net.userMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Chat state machine over [ChatRepository]. Auth is handled at the Activity layer
 * (Task 7 calls [onSignedIn] after silent/interactive sign-in), so this ViewModel needs
 * no Android context and stays unit-testable on the JVM.
 */
class ChatViewModel(
    private val repo: ChatRepository,
    private val conversations: ConversationsRepository? = null,
    // Nullable for the same reason [conversations] is: several tests build a chat with
    // neither, and approvals are a feature OF this screen rather than a precondition for
    // drawing it. Every approval entry point below is a no-op without it.
    private val approvals: ApprovalRepository? = null,
) : ViewModel() {

    /**
     * Guards the once-per-launch "start a fresh conversation" step.
     *
     * It lives on the ViewModel, not in the composition: the Activity's LaunchedEffect
     * re-runs on every configuration change (a rotation recreates the composition) while
     * the ViewModel survives it. Minting a new conversation there would cut Kadir's chat
     * in half every time he turned the phone.
     */
    private var freshConversationStarted = false
    private var reasonsFetched = false

    private val _state = MutableStateFlow(ChatUiState())
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    fun onInputChange(value: String) {
        _state.update { it.copy(input = value) }
    }

    /** Called by the host once sign-in succeeds; loads persisted history. */
    fun onSignedIn() {
        _state.update { it.copy(authPhase = AuthPhase.SIGNED_IN, error = null) }
        refreshHistory()
    }

    /**
     * This device has signed in before, so go straight to the chat — no splash, no
     * "oturum açılıyor".
     *
     * CHECKING is for the launch where we genuinely do not know yet. Once we DO know
     * (the flag is persisted across installs), waiting on Credential Manager before
     * drawing anything is a self-inflicted delay: [com.jarvis.data.net.AuthInterceptor]
     * sends the request without a header when no token is cached yet, and
     * [com.jarvis.data.net.TokenAuthenticator] refreshes on the resulting 401 and retries
     * once. The token is therefore allowed to arrive AFTER the chat is on screen.
     */
    fun onReturningUser() {
        _state.update { it.copy(authPhase = AuthPhase.SIGNED_IN, error = null) }
        refreshHistory()
    }

    /**
     * Silent re-auth found no usable credential. This is the NORMAL first-run path, not
     * a failure, so it carries no error message — it just ends [AuthPhase.CHECKING] and
     * lets the sign-in screen appear for the first time.
     *
     * It is also the ONE thing allowed to send a returning user back to the sign-in
     * screen (their account was removed from the device): the optimistic path above is
     * only a bet that the credential is still there, and this is the bet losing.
     */
    fun onSilentSignInFailed() {
        _state.update { it.copy(authPhase = AuthPhase.SIGNED_OUT) }
    }

    /** The interactive credential flow has started; clears any previous failure. */
    fun onSignInStarted() {
        _state.update { it.copy(authPhase = AuthPhase.SIGNING_IN, error = null) }
    }

    /**
     * The interactive flow failed or was cancelled. Previously this was swallowed at the
     * Activity layer, so tapping the button appeared to do nothing at all.
     */
    fun onSignInFailed(reason: String?) {
        _state.update {
            it.copy(
                authPhase = AuthPhase.SIGNED_OUT,
                error = "Giriş yapılamadı: ${reason ?: "bilinmeyen hata"}",
            )
        }
    }

    /**
     * Cold start: begin a NEW conversation rather than reopening the last one.
     *
     * Kadir's complaint was that the app "always starts from the very beginning of the
     * conversation" — one endless thread, because the session id was minted once per
     * install and never changed. A new conversation is free until it is used: the backend
     * only creates a summary row when a message is appended, so an untouched one never
     * shows up in the list.
     *
     * Idempotent across configuration changes via [freshConversationStarted].
     */
    fun onColdStart() {
        if (freshConversationStarted || conversations == null) return
        freshConversationStarted = true
        viewModelScope.launch {
            runCatching { conversations.startNew() }
            _state.update { it.copy(messages = emptyList()) }
        }
    }

    fun startNewConversation() {
        val repoRef = conversations ?: return
        _state.update { it.copy(conversationsOpen = false, error = null) }
        viewModelScope.launch {
            runCatching { repoRef.startNew() }
                .onSuccess { _state.update { s -> s.copy(messages = emptyList()) } }
                .onFailure { e ->
                    _state.update { s -> s.copy(error = "Yeni sohbet açılamadı: ${e.message}") }
                }
        }
    }

    fun toggleConversations() {
        val opening = !_state.value.conversationsOpen
        _state.update { it.copy(conversationsOpen = opening) }
        if (opening) loadConversations()
    }

    fun loadConversations() {
        val repoRef = conversations ?: return
        _state.update { it.copy(conversationsLoading = true) }
        viewModelScope.launch {
            runCatching { repoRef.list() }
                .onSuccess { rows ->
                    _state.update { it.copy(conversations = rows, conversationsLoading = false) }
                }
                .onFailure { e ->
                    _state.update {
                        it.copy(
                            conversationsLoading = false,
                            error = "Sohbetler yüklenemedi: ${e.message}",
                        )
                    }
                }
        }
    }

    fun openConversation(sessionId: String) {
        val repoRef = conversations ?: return
        _state.update { it.copy(conversationsOpen = false, error = null) }
        viewModelScope.launch {
            runCatching { repoRef.open(sessionId) }
                .onSuccess { refreshHistory() }
                .onFailure { e ->
                    _state.update { s -> s.copy(error = "Sohbet açılamadı: ${e.message}") }
                }
        }
    }

    fun deleteConversation(sessionId: String) {
        val repoRef = conversations ?: return
        viewModelScope.launch {
            runCatching { repoRef.delete(sessionId) }
                .onSuccess { movedToFresh ->
                    // Only clear the thread when the deleted conversation was the one on
                    // screen; deleting another must not wipe what the user is reading.
                    if (movedToFresh) _state.update { it.copy(messages = emptyList()) }
                    loadConversations()
                }
                .onFailure { e ->
                    _state.update { s -> s.copy(error = "Sohbet silinemedi: ${e.message}") }
                }
        }
    }

    fun refreshHistory() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val messages = repo.loadHistory()
                _state.update { it.copy(messages = messages, loading = false) }
            } catch (e: Exception) {
                _state.update { it.copy(loading = false, error = "Geçmiş yüklenemedi: ${e.message}") }
            }
            // Runs whether or not the history loaded: the queue does not depend on the
            // transcript, and a pending red action must be visible even on a launch where
            // the history call failed (spec §4.3).
            syncApprovals()
        }
    }

    /**
     * Reconciles the approval cards with the server.
     *
     * Two reads, for two different failure modes:
     *
     * 1. `GET /api/approvals` — the QUEUE. This is what makes a lost push harmless: the
     *    approval is here whether or not the notification ever arrived (spec §4.3).
     * 2. `GET /api/approvals/{id}` for every card in the transcript the queue did NOT
     *    return. Those are the decided ones, and the transcript cannot tell us their
     *    verdict — the row still reads "🔔 Onay bekliyor" and always will. The approval
     *    document is the single source of truth for the badge (spec §7). A card the queue
     *    DID return is already current, so it is not fetched twice.
     *
     * A failed queue read keeps the cards already on screen. An empty list means "no
     * approvals"; a failure means "we could not look", and quietly retracting a red action
     * that is still waiting is the worse of the two ways to be wrong.
     */
    private suspend fun syncApprovals() {
        val repoRef = approvals ?: return
        val queue = runCatching { repoRef.pending() }
        val merged = LinkedHashMap<String, Approval>()
        if (queue.isFailure) merged.putAll(_state.value.approvals)
        queue.getOrNull()?.forEach { merged[it.id] = it }

        for (id in _state.value.messages.mapNotNull { it.approvalId }.distinct()) {
            if (id in merged && queue.isSuccess) continue
            runCatching { repoRef.get(id) }.getOrNull()?.let { merged[it.id] = it }
        }

        val queueError = queue.exceptionOrNull()
            ?.userMessage("Onaylar şu an okunamıyor. Az sonra tekrar dene.")
        _state.update { it.copy(approvals = merged, error = it.error ?: queueError) }
    }

    /**
     * The voice overlay just closed; reconcile the approval queue.
     *
     * A red-zone call made DURING a voice call raises its card while this screen sits
     * inert underneath the overlay — the overlay is plain composition, so the chat gets
     * no lifecycle event when it goes away, and the queue was only synced on launch and
     * after send. Without this hook a voice-born approval stays invisible until the next
     * app launch (the 4 Ağu 01:49 case: three cards created, none on screen).
     *
     * Guarded by [ChatUiState.signedIn]: the overlay flag is also observed once at first
     * composition (LaunchedEffect), and hitting the queue before sign-in would paint
     * "Onaylar şu an okunamıyor" over the sign-in screen.
     */
    fun onVoiceCallEnded() {
        if (!_state.value.signedIn) return
        viewModelScope.launch { syncApprovals() }
    }

    /**
     * Show the approval a push notification pointed at (`data.approval_id`, spec §10).
     *
     * The id is fetched directly rather than looked up in the queue: by the time Kadir
     * taps the notification the approval may have been decided elsewhere or expired, and
     * "the card you were sent is gone" is a worse answer than showing it with its real
     * badge.
     */
    fun focusApproval(approvalId: String?) {
        val id = approvalId?.trim()?.takeIf { it.isNotEmpty() } ?: return
        _state.update { it.copy(focusedApprovalId = id) }
        val repoRef = approvals ?: return
        viewModelScope.launch {
            runCatching { repoRef.get(id) }.getOrNull()?.let { approval ->
                _state.update { it.copy(approvals = it.approvals + (approval.id to approval)) }
            }
        }
    }

    fun approveApproval(approvalId: String) {
        if (_state.value.rejectingApprovalId == approvalId) {
            _state.update { it.copy(rejectingApprovalId = null) }
        }
        decideApproval(approvalId, approve = true)
    }

    /**
     * Begins step-two rejection for [approvalId] without making a decision API call.
     * Lazily fetches rejection reasons ONCE per session.
     */
    fun beginReject(approvalId: String) {
        _state.update { it.copy(rejectingApprovalId = approvalId) }
        if (!reasonsFetched && approvals != null) {
            reasonsFetched = true
            viewModelScope.launch {
                val reasons = runCatching { approvals.reasons() }.getOrDefault(emptyList())
                _state.update { it.copy(rejectionReasons = reasons) }
            }
        }
    }

    /** Clears the current step-two rejection state. */
    fun cancelReject() {
        _state.update { it.copy(rejectingApprovalId = null) }
    }

    /**
     * Confirms rejection using [reason] for the active [ChatUiState.rejectingApprovalId].
     * Clears [ChatUiState.rejectingApprovalId] on attempt regardless of network outcome.
     */
    fun confirmReject(reason: String) {
        val id = _state.value.rejectingApprovalId ?: return
        _state.update { it.copy(rejectingApprovalId = null) }
        decideApproval(id, approve = false, reason = reason)
    }

    /**
     * Single-argument overload kept for MainActivity compatibility (`vm::rejectApproval`).
     * Routes through [beginReject] semantics for step-two selection.
     */
    fun rejectApproval(approvalId: String) = beginReject(approvalId)

    /**
     * Direct rejection with a specific [reason].
     */
    fun rejectApproval(approvalId: String, reason: String?) =
        decideApproval(approvalId, approve = false, reason = reason)

    /**
     * Sends a decision and adopts the status the SERVER returned.
     *
     * NO OPTIMISTIC UPDATE — the 3d-3 lesson. The button says "approve"; the server may
     * answer `expired`, because the TTL is enforced at decision time and not only by the
     * sweeper (spec §4.1). A card that flipped to "Onaylandı" on tap would be claiming a
     * red action ran when it never did. On failure the card does not move at all: it stays
     * pending and decidable, so a retry is one tap away.
     *
     * The guard is [ChatUiState.canDecide], the same flag the buttons are drawn from, so a
     * double tap sends one decision and an already-decided card sends none.
     */
    private fun decideApproval(approvalId: String, approve: Boolean, reason: String? = null) {
        val repoRef = approvals ?: return
        if (!_state.value.canDecide(approvalId)) return
        val trimmedReason = reason?.trim().orEmpty()
        if (!approve && trimmedReason.isEmpty()) {
            _state.update { it.copy(error = "Reddetmek için bir gerekçe seçin.") }
            return
        }
        _state.update {
            it.copy(decidingApprovals = it.decidingApprovals + approvalId, error = null)
        }
        viewModelScope.launch {
            val result = runCatching {
                if (approve) repoRef.approve(approvalId)
                else repoRef.reject(approvalId, trimmedReason)
            }
            _state.update { s ->
                val stillDeciding = s.decidingApprovals - approvalId
                result.fold(
                    onSuccess = { decision ->
                        val current = s.approvals[approvalId]
                        s.copy(
                            decidingApprovals = stillDeciding,
                            approvals = if (current == null) s.approvals else s.approvals +
                                (approvalId to current.copy(
                                    status = decision.status,
                                    outcome = decision.outcome ?: current.outcome,
                                )),
                        )
                    },
                    onFailure = { e ->
                        s.copy(
                            decidingApprovals = stillDeciding,
                            error = e.userMessage(
                                if (approve) "Onaylanamadı. Az sonra tekrar dene."
                                else "Reddedilemedi. Az sonra tekrar dene.",
                            ),
                        )
                    },
                )
            }
            // The claim race (approvals.py: the loser re-reads the document, and the
            // winner may not have written its status projection yet) can answer
            // `already` while still reporting "pending". Adopted as-is, the card would
            // sit there looking decidable although the action had already run. One
            // re-read settles it; nothing else depends on it, so a failure is ignored.
            val decision = result.getOrNull()
            if (decision != null && decision.already &&
                decision.status == ApprovalStatus.PENDING
            ) {
                runCatching { repoRef.get(approvalId) }.getOrNull()?.let { fresh ->
                    _state.update { it.copy(approvals = it.approvals + (fresh.id to fresh)) }
                }
            }
        }
    }

    fun send() {
        val text = _state.value.input.trim()
        if (text.isEmpty() || _state.value.sending) return

        // Optimistic: show the user bubble immediately, clear the input, mark sending.
        _state.update {
            it.copy(
                messages = it.messages + UiMessage(role = "user", text = text),
                input = "",
                sending = true,
                error = null,
            )
        }
        viewModelScope.launch {
            try {
                val reply = repo.send(text)
                _state.update { it.copy(messages = it.messages + reply, sending = false) }
            } catch (e: Exception) {
                // Roll back the optimistic bubble and restore the text so it isn't lost.
                _state.update {
                    it.copy(
                        messages = it.messages.dropLast(1),
                        input = text,
                        sending = false,
                        error = e.userMessage("Gönderilemedi. Az sonra tekrar dene."),
                    )
                }
                return@launch
            }
            // A turn can RAISE an approval: a red-zone tool call becomes a pending card
            // via the server's sink (spec §5), which writes it into the transcript and
            // pushes it. The transcript is not re-read here (the reply is appended
            // locally), so without this the card is invisible in exactly the flow that
            // created it — Kadir reads "onay kartı gönderdim" and sees no card, with no
            // pull-to-refresh to rescue him.
            //
            // OUTSIDE the try: the catch above rolls back by dropping the LAST message,
            // which by this point is the reply, not the optimistic bubble. A stumble
            // while syncing must never delete a reply that was actually delivered.
            syncApprovals()
        }
    }
}
