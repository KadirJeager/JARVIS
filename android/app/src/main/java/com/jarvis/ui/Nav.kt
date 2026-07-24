package com.jarvis.ui

import androidx.compose.runtime.Composable
import com.jarvis.ui.auth.BootSplash
import com.jarvis.ui.auth.SignInScreen
import com.jarvis.ui.chat.AuthPhase
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState

/**
 * Top-level switch over the four auth phases.
 *
 * It switches on [AuthPhase], not on a `signedIn` boolean, because "not signed in yet"
 * and "not signed in" are different screens: the first launch frame is CHECKING, and
 * showing the sign-in screen then flashed a login prompt at an already-authorized user
 * on every single warm start.
 */
@Composable
fun Nav(
    state: ChatUiState,
    onSignIn: () -> Unit,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
) {
    when (state.authPhase) {
        AuthPhase.CHECKING -> BootSplash()
        AuthPhase.SIGNED_OUT, AuthPhase.SIGNING_IN -> SignInScreen(
            onSignIn = onSignIn,
            signingIn = state.authPhase == AuthPhase.SIGNING_IN,
            error = state.error,
        )
        AuthPhase.SIGNED_IN -> ChatScreen(
            state = state,
            onInput = onInput,
            onSend = onSend,
            onRetry = onRetry,
        )
    }
}
