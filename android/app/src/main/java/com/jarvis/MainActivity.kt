package com.jarvis

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.rememberUpdatedState
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
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
import com.jarvis.data.voice.session.AndroidClipRecorder
import com.jarvis.data.voice.session.VoicePhase
import com.jarvis.ui.theme.JarvisTheme
import com.jarvis.data.wear.PairResult
import com.jarvis.ui.voice.EnrollDeviceViewModel
import com.jarvis.ui.voice.VoiceProfileViewModel
import com.jarvis.ui.voicecall.VoiceCallOverlay
import com.jarvis.ui.voicecall.VoiceCallViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/**
 * The intent extra that carries an approval id into the app.
 *
 * The name is the WIRE key, not a local invention: `fcm.send_approval` sends
 * `data.approval_id`, and Firebase copies a notification's `data` map into the launch
 * intent's extras verbatim. One concept, one name, end to end.
 *
 * Two producers put it here, and neither is optional. Backgrounded, the system tray draws
 * the push and Firebase copies `data` into the launch intent verbatim; in the foreground
 * [JarvisFCMService] builds the notification and sets the extra itself. Both land on the
 * same key, which is why it is read here once and nowhere else.
 */
const val EXTRA_APPROVAL_ID = "approval_id"

class MainActivity : FragmentActivity() {

    /**
     * The approval id a notification tap carried in, waiting to be handed to the
     * ChatViewModel.
     *
     * A flow rather than a read of `intent` at composition time, because the launch is
     * not the only way one arrives. When the app is already running, tapping a
     * notification delivers the extra through [onNewIntent] — no recomposition, so a
     * `LaunchedEffect(Unit)` that read `intent` would never see it and the tap would
     * land on the chat with no card focused. That is also the ONLY path the app does
     * not build itself: `fcm.send_approval` sends a `notification` payload, so a
     * backgrounded app gets the system tray's own PendingIntent, not
     * JarvisFCMService's.
     *
     * Paired with `android:launchMode="singleTop"`: without it the tap would stack a
     * SECOND MainActivity instead of calling onNewIntent on the live one.
     */
    private val pendingApprovalId = MutableStateFlow<String?>(null)

    override fun onNewIntent(intent: android.content.Intent) {
        super.onNewIntent(intent)
        // setIntent so anything later reading getIntent() sees the new one, not the
        // launch intent -- the standard singleTop contract.
        setIntent(intent)
        intent.getStringExtra(EXTRA_APPROVAL_ID)?.let { pendingApprovalId.value = it }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JarvisApp).container
        intent?.getStringExtra(EXTRA_APPROVAL_ID)?.let { pendingApprovalId.value = it }
        setContent {
            JarvisTheme {
                val vm: ChatViewModel = viewModel {
                    ChatViewModel(
                        container.chatRepository,
                        container.conversationsRepository,
                        container.approvalRepository,
                    )
                }
                val state by vm.state.collectAsState()
                val scope = rememberCoroutineScope()

                val voiceVm: VoiceProfileViewModel =
                    viewModel { VoiceProfileViewModel(container.voiceProfileRepository) }
                val voiceState by voiceVm.state.collectAsState()

                // Task 9: "Bu cihazı tanıt". deviceHint matches JarvisApp.kt's own
                // "android-" + Build.MODEL verbatim -- otherwise enrollment would write
                // anchors under a channel label the live voice bridge never sends.
                val enrollVm: EnrollDeviceViewModel = viewModel {
                    EnrollDeviceViewModel(
                        container.voiceApi,
                        AndroidClipRecorder(),
                        "android-" + android.os.Build.MODEL,
                    )
                }
                val enrollState by enrollVm.state.collectAsState()
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
                // isAvailable() is not checked here: BiometricPrompt.authenticate()
                // already fails closed via onAuthenticationError when no device lock
                // is configured, landing on the same DENIED state through
                // onUnlockFailed — a separate isAvailable() branch would just be a
                // second path to the same user-visible outcome.
                fun armGate() {
                    voiceVm.onGateRequested()
                    container.biometricGate.prompt(this@MainActivity) { result ->
                        result.fold(
                            onSuccess = { voiceVm.onUnlocked() },
                            onFailure = { voiceVm.onUnlockFailed(it.message) },
                        )
                    }
                }

                fun openVoiceProfile() {
                    armGate()
                    route = Route.VOICE_PROFILE
                }

                // FLAG_SECURE rides exactly with the voice screen: its content must not
                // land in screenshots or the recents preview, while the chat stays
                // screenshotable (Kadir screenshots his own chats).
                LaunchedEffect(route) {
                    if (route == Route.VOICE_PROFILE) {
                        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    } else {
                        window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE)
                    }
                }

                // Returning from the BACKGROUND onto the voice screen re-runs the gate.
                // ON_START, not ON_RESUME: BiometricPrompt's sheet only PAUSES the
                // activity, so a resume-keyed gate would loop prompt→pause→resume→prompt
                // forever. addObserver replays lifecycle up to the current state, so the
                // first replayed ON_START fires while route is still CHAT and no-ops.
                val currentRoute by rememberUpdatedState(route)
                DisposableEffect(Unit) {
                    val observer = LifecycleEventObserver { _, event ->
                        if (event == Lifecycle.Event.ON_START && currentRoute == Route.VOICE_PROFILE) {
                            armGate()
                        }
                    }
                    lifecycle.addObserver(observer)
                    onDispose { lifecycle.removeObserver(observer) }
                }

                // Hand-rolled Nav is invisible to the platform back dispatcher: without
                // this, the system Back button exits the app from the voice screen
                // instead of returning to chat.
                BackHandler(enabled = route == Route.VOICE_PROFILE) { route = Route.CHAT }

                val voiceCallVm: VoiceCallViewModel = viewModel {
                    VoiceCallViewModel(
                        container.voiceSessionFactory,
                        // Result deliberately dropped: on refresh failure the session
                        // dials with whatever token is cached and the server's own
                        // rejection reaches the overlay as a visible error.
                        refreshAuth = { container.authManager.silentSignIn() },
                    )
                }
                val voiceCallState by voiceCallVm.state.collectAsState()

                // The system permission dialog resolves asynchronously; a denial must
                // surface as a visible error, not a button that silently does nothing.
                // POST_NOTIFICATIONS rides along on Android 13+ so the in-call
                // foreground-service notification is actually visible — but only the
                // MIC decides the call: a denied notification never blocks dialing.
                val voicePermissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestMultiplePermissions(),
                ) { grants ->
                    // The mic may not be IN this result at all (already held, only the
                    // notification permission was asked) — absent means "check current
                    // state", not "denied".
                    val micOk = grants[Manifest.permission.RECORD_AUDIO]
                        ?: (ContextCompat.checkSelfPermission(
                            this@MainActivity,
                            Manifest.permission.RECORD_AUDIO,
                        ) == PackageManager.PERMISSION_GRANTED)
                    if (micOk) voiceCallVm.start() else voiceCallVm.onMicPermissionDenied()
                }

                fun startVoice() {
                    fun missing(permission: String) = ContextCompat.checkSelfPermission(
                        this@MainActivity, permission,
                    ) != PackageManager.PERMISSION_GRANTED

                    val wanted = buildList {
                        if (missing(Manifest.permission.RECORD_AUDIO)) add(Manifest.permission.RECORD_AUDIO)
                        if (android.os.Build.VERSION.SDK_INT >= 33 &&
                            missing(Manifest.permission.POST_NOTIFICATIONS)
                        ) {
                            add(Manifest.permission.POST_NOTIFICATIONS)
                        }
                    }
                    if (wanted.isEmpty()) voiceCallVm.start()
                    else voicePermissionLauncher.launch(wanted.toTypedArray())
                }

                // Wear W1 Task 3: mint + push, one shot. No new screen and no new
                // persisted state -- the result is reported once via a plain Toast,
                // matching this app's lightest existing feedback surface.
                fun pairWatch() {
                    scope.launch {
                        val message = when (val result = container.watchPairing.pair()) {
                            is PairResult.Sent -> "Saat eşleştirildi"
                            PairResult.NoWatch -> "Bağlı saat bulunamadı"
                            is PairResult.Failed -> result.userMessage
                        }
                        Toast.makeText(this@MainActivity, message, Toast.LENGTH_SHORT).show()
                    }
                }

                // A push notification carries `data.approval_id` (fcm.send_approval,
                // spec §10); tapping it must land on THAT card. Keyed on the flow, not
                // on Unit, so a tap that arrives while the app is already running
                // (onNewIntent -- no recomposition) is delivered too. Consumed on
                // arrival so a rotation cannot re-focus a card Kadir has scrolled away
                // from.
                val approvalToFocus by pendingApprovalId.collectAsState()
                LaunchedEffect(approvalToFocus) {
                    approvalToFocus?.let {
                        vm.focusApproval(it)
                        pendingApprovalId.value = null
                    }
                }

                // POST_NOTIFICATIONS on its own launcher, not only inside startVoice():
                // approvals reach Kadir by push and North Star §4.8 puts that on the
                // critical path, so an install that never happens to place a voice call
                // would otherwise drop every approval notification silently. Asked once
                // per launch and only when actually missing; a denial changes nothing
                // else -- the queue still syncs on open (spec §4.3).
                val notificationPermissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestPermission(),
                ) { /* granted or not, nothing else depends on it */ }
                LaunchedEffect(Unit) {
                    if (android.os.Build.VERSION.SDK_INT >= 33 &&
                        ContextCompat.checkSelfPermission(
                            this@MainActivity, Manifest.permission.POST_NOTIFICATIONS,
                        ) != PackageManager.PERMISSION_GRANTED
                    ) {
                        notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                    }
                }

                // While a call (or its error) owns the screen, Back hangs up instead of
                // exiting the app under a live microphone.
                BackHandler(enabled = voiceCallState.phase != VoicePhase.IDLE) {
                    voiceCallVm.stop()
                }

                // A LIVE call (not an already-dead ERROR screen) holds the mic
                // foreground service, so screen-off or another app in front cannot
                // kill the capture or the socket. Keyed on a Boolean, not the phase:
                // CONNECTING→LISTENING→SPEAKING must not restart the service.
                val callLive = voiceCallState.phase == VoicePhase.CONNECTING ||
                    voiceCallState.phase == VoicePhase.LISTENING ||
                    voiceCallState.phase == VoicePhase.SPEAKING
                LaunchedEffect(callLive) {
                    if (callLive) VoiceCallService.start(this@MainActivity)
                    else VoiceCallService.stop(this@MainActivity)
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
                        // Only now: /api/fcm/register is require_user, so before a session
                        // exists this is a guaranteed 401. Last in the branch because a
                        // push registration must never delay the chat becoming usable.
                        container.registerForPush()
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
                        onStartEnrollDevice = enrollVm::start,
                        onProceedToRecording = enrollVm::proceedToRecording,
                    ),
                    enrollState = enrollState,
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
                                    // The FIRST-EVER launch never reaches the boot path's
                                    // registration (silent sign-in fails before an account
                                    // is chosen). Without this line a brand-new install
                                    // would receive no push until its second launch.
                                    container.registerForPush()
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
                    onApproveApproval = vm::approveApproval,
                    onRejectApproval = vm::rejectApproval,
                    onPairWatch = { pairWatch() },
                )

                // The overlay is plain composition: closing it fires no lifecycle event
                // at the chat underneath, so a voice-born approval card stayed invisible
                // until the next launch (the 4 Ağu 01:49 case). The flag flip IS the
                // close event; the ViewModel ignores the first "closed" observation that
                // this effect makes at initial composition (sign-in guard).
                val inVoiceCall = voiceCallState.phase != VoicePhase.IDLE
                LaunchedEffect(inVoiceCall) {
                    if (!inVoiceCall) vm.onVoiceCallEnded()
                }

                // Drawn AFTER (= on top of) Nav: while a call is anything but IDLE the
                // overlay owns the screen. Dismissing an error is also just stop() —
                // the session is already torn down, this only returns the state to IDLE.
                if (inVoiceCall) {
                    VoiceCallOverlay(
                        state = voiceCallState,
                        onStop = voiceCallVm::stop,
                        onDismissError = voiceCallVm::stop,
                        onInterrupt = voiceCallVm::interrupt,
                    )
                }
            }
        }
    }
}
