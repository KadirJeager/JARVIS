package com.jarvis.ui.chat

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
) {
    /** Derived, never stored: one source of truth for "is the session live". */
    val signedIn: Boolean get() = authPhase == AuthPhase.SIGNED_IN
}
