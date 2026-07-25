package com.jarvis.ui

import androidx.compose.runtime.Composable
import com.jarvis.ui.auth.BootSplash
import com.jarvis.ui.auth.SignInScreen
import com.jarvis.ui.chat.AuthPhase
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState
import com.jarvis.ui.voice.VoiceProfileScreen
import com.jarvis.ui.voice.VoiceProfileUiState

/** Which signed-in screen is showing. Hand-rolled: two destinations do not justify
 *  pulling in navigation-compose, and the project deliberately has no nav library. */
enum class Route { CHAT, VOICE_PROFILE }

/** The voice screen's callbacks, bundled so Nav's signature stays readable. */
class VoiceActions(
    val onRetryUnlock: () -> Unit,
    val onRetryLoad: () -> Unit,
    val onSetLabel: (String, String?) -> Unit,
    val onDeleteSample: (String) -> Unit,
    val onConfirm: (String) -> Unit,
    val onReject: (String) -> Unit,
    val onDeleteProfile: () -> Unit,
    val onDismissError: () -> Unit,
)

/**
 * Top-level switch over the four auth phases, then over [Route] once signed in.
 *
 * It switches on [AuthPhase], not on a `signedIn` boolean, because "not signed in yet"
 * and "not signed in" are different screens: the first launch frame is CHECKING, and
 * showing the sign-in screen then flashed a login prompt at an already-authorized user
 * on every single warm start.
 */
@Composable
fun Nav(
    state: ChatUiState,
    route: Route,
    voiceState: VoiceProfileUiState,
    voiceActions: VoiceActions,
    onSignIn: () -> Unit,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
    onOpenVoiceProfile: () -> Unit,
    onBack: () -> Unit,
    onToggleConversations: () -> Unit = {},
    onNewConversation: () -> Unit = {},
    onOpenConversation: (String) -> Unit = {},
    onDeleteConversation: (String) -> Unit = {},
) {
    when (state.authPhase) {
        AuthPhase.CHECKING -> BootSplash()
        AuthPhase.SIGNED_OUT, AuthPhase.SIGNING_IN -> SignInScreen(
            onSignIn = onSignIn,
            signingIn = state.authPhase == AuthPhase.SIGNING_IN,
            error = state.error,
        )
        AuthPhase.SIGNED_IN -> when (route) {
            Route.CHAT -> ChatScreen(
                state = state,
                onInput = onInput,
                onSend = onSend,
                onRetry = onRetry,
                onOpenVoiceProfile = onOpenVoiceProfile,
                onToggleConversations = onToggleConversations,
                onNewConversation = onNewConversation,
                onOpenConversation = onOpenConversation,
                onDeleteConversation = onDeleteConversation,
            )
            Route.VOICE_PROFILE -> VoiceProfileScreen(
                state = voiceState,
                onBack = onBack,
                onRetryUnlock = voiceActions.onRetryUnlock,
                onRetryLoad = voiceActions.onRetryLoad,
                onSetLabel = voiceActions.onSetLabel,
                onDeleteSample = voiceActions.onDeleteSample,
                onConfirm = voiceActions.onConfirm,
                onReject = voiceActions.onReject,
                onDeleteProfile = voiceActions.onDeleteProfile,
                onDismissError = voiceActions.onDismissError,
            )
        }
    }
}
