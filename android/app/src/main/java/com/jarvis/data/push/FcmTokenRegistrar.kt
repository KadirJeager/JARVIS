package com.jarvis.data.push

import com.jarvis.data.net.FcmApi
import com.jarvis.data.net.FcmTokenRequest
import kotlinx.coroutines.CancellationException

/**
 * What one registration attempt actually did. An enum rather than a Boolean because the
 * three "nothing was sent" cases are NOT the same event, and a log line that cannot tell
 * them apart cannot localize the fault: signed out is expected on a cold first launch,
 * no token means Firebase itself never produced one (no Play Services, no network), and
 * [FAILED] means the server refused a request we really did send.
 */
enum class FcmRegistration {
    /** The server accepted the token. */
    REGISTERED,

    /** No session yet — `POST /api/fcm/register` is `require_user` and would only 401. */
    SKIPPED_SIGNED_OUT,

    /** Firebase produced no token; nothing to register. */
    SKIPPED_NO_TOKEN,

    /** This process already registered this exact token. */
    SKIPPED_ALREADY,

    /** A request went out and the server refused it (401, 502, offline, ...). */
    FAILED,
}

/**
 * Registers this device's FCM token with the backend — the producing half of the push
 * path, and the reason `fcm_tokens` was empty in production until Faz Y3: the server has
 * been sending pushes since Y2.4, but nothing on the phone ever called the register
 * endpoint, so EVERY "live FCM test" silently ran the chat fallback instead
 * (`{'reason':'no_fcm_tokens','fallback':'chat_reminders'}`).
 *
 * Deliberately free of every Android type — no `Context`, no `FirebaseMessaging`, no
 * `android.util.Log` (which throws "not mocked" under a JVM test). That is what lets the
 * whole decision table be pinned by a plain JVM test against MockWebServer
 * (`FcmWireTest`), instead of only by an instrumented run nobody executes on every commit.
 * [JarvisFCMService] and [com.jarvis.AppContainer] are the thin shells that hand it the
 * Android-shaped inputs.
 *
 * Two rules earn their place here:
 *
 * - **`onNewToken` cannot be the only trigger.** Firebase mints the token once per install
 *   and then never calls `onNewToken` again. An app that only listened for it would
 *   register exactly zero times on a device whose token predates this code — which is
 *   every device that already has the app. Hence [registerCurrentToken], called on every
 *   app open once a session exists.
 * - **Signed out means nothing is sent at all.** Not "sent and 401s": [isSignedIn] is
 *   checked before the call, because a request with no Bearer header is a guaranteed
 *   round trip to a guaranteed rejection.
 */
class FcmTokenRegistrar(
    private val api: FcmApi,
    private val isSignedIn: () -> Boolean,
    private val currentToken: suspend () -> String?,
) {

    /**
     * The last token this process successfully registered. Volatile because the two
     * callers sit on different threads: `onNewToken` runs on Firebase's executor while
     * the app-open path runs from the Activity's scope.
     *
     * Per PROCESS, not persisted, and that is the intent: a cheap guard against
     * re-POSTing the same token on every rotation (`LaunchedEffect(Unit)` re-runs when
     * the Activity is recreated), while a fresh process still re-asserts the token — so
     * a token dropped server-side heals on the next launch rather than never.
     */
    @Volatile
    private var lastRegistered: String? = null

    /**
     * Asks Firebase for the token this device already holds and registers it. This is the
     * app-open path; it is idempotent by construction — the server keys the document on
     * `sha256(token)`, so a repeat is an overwrite, never a duplicate device.
     */
    suspend fun registerCurrentToken(): FcmRegistration = register(currentToken())

    /**
     * Registers [token]. Never throws for a server-side or transport failure: a push
     * registration that fails must not take down the caller — the boot path in
     * `MainActivity`, or Firebase's own `onNewToken` callback. A lost registration only
     * costs the push; the approval itself is still in the transcript and the queue is
     * re-synced from `GET /api/approvals` on every launch (spec §4.3).
     */
    suspend fun register(token: String?): FcmRegistration {
        val trimmed = token?.trim().orEmpty()
        if (trimmed.isEmpty()) return FcmRegistration.SKIPPED_NO_TOKEN
        if (!isSignedIn()) return FcmRegistration.SKIPPED_SIGNED_OUT
        if (trimmed == lastRegistered) return FcmRegistration.SKIPPED_ALREADY
        return try {
            api.register(FcmTokenRequest(trimmed))
            lastRegistered = trimmed
            FcmRegistration.REGISTERED
        } catch (e: CancellationException) {
            // Structured concurrency: a cancelled scope is not a registration failure and
            // must keep propagating, or the caller's coroutine never learns it was killed.
            throw e
        } catch (_: Exception) {
            // Deliberately NOT recorded in lastRegistered — the next attempt must retry.
            FcmRegistration.FAILED
        }
    }
}
