package com.jarvis

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jarvis.ui.Nav
import com.jarvis.ui.Route
import com.jarvis.ui.VoiceActions
import com.jarvis.ui.chat.ChatViewModel
import com.jarvis.ui.theme.JarvisTheme
import com.jarvis.ui.voice.VoiceProfileViewModel
import kotlinx.coroutines.launch

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JarvisApp).container
        setContent {
            JarvisTheme {
                val vm: ChatViewModel = viewModel { ChatViewModel(container.chatRepository) }
                val state by vm.state.collectAsState()
                val scope = rememberCoroutineScope()

                val voiceVm: VoiceProfileViewModel =
                    viewModel { VoiceProfileViewModel(container.voiceProfileRepository) }
                val voiceState by voiceVm.state.collectAsState()
                var route by remember { mutableStateOf(Route.CHAT) }

                // Whole-profile deletion leaves nothing to show; drop back to the chat.
                LaunchedEffect(voiceState.deleted) {
                    if (voiceState.deleted) route = Route.CHAT
                }

                // The gate runs on ENTERING the screen, not once per process: leaving and
                // coming back must ask again, otherwise the lock protects only the first
                // visit of the session.
                fun openVoiceProfile() {
                    route = Route.VOICE_PROFILE
                    container.biometricGate.prompt(this@MainActivity) { result ->
                        result.fold(
                            onSuccess = { voiceVm.onUnlocked() },
                            onFailure = { voiceVm.onUnlockFailed(it.message) },
                        )
                    }
                }

                // Boot: try silent re-auth. Both outcomes must be reported -- the UI
                // stays on the boot splash until one of them lands, so swallowing the
                // failure would hang the app on the splash forever.
                LaunchedEffect(Unit) {
                    if (container.authManager.silentSignIn().isSuccess) vm.onSignedIn()
                    else vm.onSilentSignInFailed()
                }

                Nav(
                    state = state,
                    route = route,
                    voiceState = voiceState,
                    voiceActions = VoiceActions(
                        onRetryUnlock = { openVoiceProfile() },
                        onRetryLoad = voiceVm::load,
                        onSetLabel = voiceVm::setLabel,
                        onDeleteSample = voiceVm::deleteSample,
                        onConfirm = voiceVm::confirm,
                        onReject = voiceVm::reject,
                        onDeleteProfile = voiceVm::deleteProfile,
                        onDismissError = voiceVm::dismissError,
                    ),
                    onSignIn = {
                        scope.launch {
                            vm.onSignInStarted()
                            val result = container.authManager.signIn(this@MainActivity)
                            // The failure branch used to be absent entirely, so a
                            // cancelled or failed credential flow left the button
                            // looking simply broken.
                            result.fold(
                                onSuccess = { vm.onSignedIn() },
                                onFailure = { vm.onSignInFailed(it.message) },
                            )
                        }
                    },
                    onInput = vm::onInputChange,
                    onSend = vm::send,
                    onRetry = vm::refreshHistory,
                    onOpenVoiceProfile = { openVoiceProfile() },
                    onBack = { route = Route.CHAT },
                )
            }
        }
    }
}
