package com.jarvis.ui

import androidx.compose.runtime.Composable
import com.jarvis.ui.auth.SignInScreen
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState

/** Top-level switch: signed-in shows the chat thread, otherwise the sign-in screen. */
@Composable
fun Nav(
    state: ChatUiState,
    onSignIn: () -> Unit,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
) {
    if (state.signedIn) {
        ChatScreen(state = state, onInput = onInput, onSend = onSend, onRetry = onRetry)
    } else {
        SignInScreen(onSignIn = onSignIn)
    }
}
