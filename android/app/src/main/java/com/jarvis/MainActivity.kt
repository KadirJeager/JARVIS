package com.jarvis

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jarvis.ui.Nav
import com.jarvis.ui.Route
import com.jarvis.ui.VoiceActions
import com.jarvis.ui.chat.ChatViewModel
import com.jarvis.data.voice.session.VoicePhase
import com.jarvis.ui.theme.JarvisTheme
import com.jarvis.ui.voice.VoiceProfileViewModel
import com.jarvis.ui.voicecall.VoiceCallOverlay
import com.jarvis.ui.voicecall.VoiceCallViewModel
import kotlinx.coroutines.launch

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JarvisApp).container
        setContent {
            JarvisTheme {
                val vm: ChatViewModel = viewModel {
                    ChatViewModel(container.chatRepository, container.conversationsRepository)
                }
                val state by vm.state.collectAsState()
                val scope = rememberCoroutineScope()

                val voiceVm: VoiceProfileViewModel =
                    viewModel { VoiceProfileViewModel(container.voiceProfileRepository) }
                val voiceState by voiceVm.state.collectAsState()
                // rememberSaveable, not remember: a rotation while on the voice screen
                // must not silently drop the user back to chat. Safe now that every entry
                // re-locks (see onGateRequested below) — a restored VOICE_PROFILE route
                // still starts from CHECKING, with no stale content to leak.
                var route by rememberSaveable { mutableStateOf(Route.CHAT) }

                // Whole-profile deletion leaves nothing to show; drop back to the chat.
                LaunchedEffect(voiceState.deleted) {
                    if (voiceState.deleted) route = Route.CHAT
                }

                // The gate runs on ENTERING the screen, not once per process: leaving and
                // coming back must ask again, otherwise the lock protects only the first
                // visit of the session. onGateRequested() runs FIRST and SYNCHRONOUSLY,
                // before the route flips and before the (asynchronous) prompt is shown:
                // route flipping alone would otherwise render the previous visit's whole
                // profile for as long as the prompt sheet takes to appear, since
                // BiometricPrompt is a translucent bottom sheet, not an opaque cover.
                fun openVoiceProfile() {
                    voiceVm.onGateRequested()
                    route = Route.VOICE_PROFILE
                    // isAvailable() is not checked here: BiometricPrompt.authenticate()
                    // already fails closed via onAuthenticationError when no device lock
                    // is configured, landing on the same DENIED state through
                    // onUnlockFailed — a separate isAvailable() branch would just be a
                    // second path to the same user-visible outcome.
                    container.biometricGate.prompt(this@MainActivity) { result ->
                        result.fold(
                            onSuccess = { voiceVm.onUnlocked() },
                            onFailure = { voiceVm.onUnlockFailed(it.message) },
                        )
                    }
                }

                // Hand-rolled Nav is invisible to the platform back dispatcher: without
                // this, the system Back button exits the app from the voice screen
                // instead of returning to chat.
                BackHandler(enabled = route == Route.VOICE_PROFILE) { route = Route.CHAT }

                val voiceCallVm: VoiceCallViewModel =
                    viewModel { VoiceCallViewModel(container.voiceSessionFactory) }
                val voiceCallState by voiceCallVm.state.collectAsState()

                // The system permission dialog resolves asynchronously; a denial must
                // surface as a visible error, not a button that silently does nothing.
                val micPermissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestPermission(),
                ) { granted ->
                    if (granted) voiceCallVm.start() else voiceCallVm.onMicPermissionDenied()
                }

                fun startVoice() {
                    val granted = ContextCompat.checkSelfPermission(
                        this@MainActivity,
                        Manifest.permission.RECORD_AUDIO,
                    ) == PackageManager.PERMISSION_GRANTED
                    if (granted) voiceCallVm.start()
                    else micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                }

                // While a call (or its error) owns the screen, Back hangs up instead of
                // exiting the app under a live microphone.
                BackHandler(enabled = voiceCallState.phase != VoicePhase.IDLE) {
                    voiceCallVm.stop()
                }

                // Boot.
                //
                // A returning user goes STRAIGHT to the chat and the credential is
                // fetched behind it. Previously every launch parked on the splash until
                // Credential Manager answered, which is what "oturum açılıyor" spinning
                // on every start actually was. Nothing depends on the token being present
                // by first frame: AuthInterceptor omits the header when there is no
                // cached token, and TokenAuthenticator refreshes on the 401 and retries.
                //
                // Only a genuinely missing credential (account removed from the device)
                // sends a returning user back to sign-in — and it clears the flag so the
                // next launch does not make the same optimistic bet.
                LaunchedEffect(Unit) {
                    // Every launch opens a NEW conversation; the old ones live in the list.
                    vm.onColdStart()
                    val returning = container.authStateStore.hasSignedInBefore()
                    if (returning) vm.onReturningUser()

                    if (container.authManager.silentSignIn().isSuccess) {
                        container.authStateStore.markSignedIn()
                        if (!returning) vm.onSignedIn()
                    } else if (returning) {
                        container.authStateStore.clearSignedIn()
                        vm.onSilentSignInFailed()
                    } else {
                        vm.onSilentSignInFailed()
                    }
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
                                onSuccess = {
                                    // Remember it, so every later launch skips the splash.
                                    container.authStateStore.markSignedIn()
                                    vm.onSignedIn()
                                },
                                onFailure = { vm.onSignInFailed(it.message) },
                            )
                        }
                    },
                    onInput = vm::onInputChange,
                    onSend = vm::send,
                    onRetry = vm::refreshHistory,
                    onOpenVoiceProfile = { openVoiceProfile() },
                    onStartVoice = { startVoice() },
                    onBack = { route = Route.CHAT },
                    onToggleConversations = vm::toggleConversations,
                    onNewConversation = vm::startNewConversation,
                    onOpenConversation = vm::openConversation,
                    onDeleteConversation = vm::deleteConversation,
                )

                // Drawn AFTER (= on top of) Nav: while a call is anything but IDLE the
                // overlay owns the screen. Dismissing an error is also just stop() —
                // the session is already torn down, this only returns the state to IDLE.
                if (voiceCallState.phase != VoicePhase.IDLE) {
                    VoiceCallOverlay(
                        state = voiceCallState,
                        onStop = voiceCallVm::stop,
                        onDismissError = voiceCallVm::stop,
                    )
                }
            }
        }
    }
}
