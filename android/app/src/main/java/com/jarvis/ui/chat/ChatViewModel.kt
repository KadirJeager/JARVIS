package com.jarvis.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.ConversationsRepository
import com.jarvis.data.chat.UiMessage
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
                        error = "Gönderilemedi: ${e.message}",
                    )
                }
            }
        }
    }
}
