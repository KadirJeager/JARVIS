package com.jarvis.data.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessaging
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine

/** One tag for the whole push path, so a field report is a single `logcat -s` away. */
const val PUSH_LOG_TAG = "JarvisPush"

/**
 * The token this device already holds, or null if Firebase cannot produce one.
 *
 * This exists because `FirebaseMessagingService.onNewToken` fires only when the token
 * CHANGES — on a device where it was minted before this code shipped it never fires
 * again, and a registration path built on it alone would register nothing, forever.
 *
 * `addOnCompleteListener` rather than success/failure listeners: it fires exactly once in
 * both outcomes, so the continuation can never be resumed twice (which crashes) nor left
 * hanging (which would stall the boot coroutine). A missing token is returned as null and
 * logged, never thrown — no push is a degraded app, not a broken one.
 *
 * `getToken()` is deprecated in firebase-messaging 25.x and is used anyway, deliberately.
 * Its stated replacement, `register()`, returns `Task<Void>`: it triggers registration and
 * delivers the token ONLY through `FirebaseMessagingService.onRegistered`, i.e. only when
 * the token changes — which is precisely the callback-shaped hole this function exists to
 * fill. It is also still functional here: `getToken()` fails only when
 * `GmsRegistrationClient.isV1RegistrationEnabled()` is true, and that reads the manifest
 * meta-data `firebase_messaging_installation_id_enabled`, which this app does not declare
 * (absent = false). If that meta-data is ever added, this path starts returning null and
 * the log line below is what will say so.
 */
@Suppress("DEPRECATION")
suspend fun firebaseMessagingToken(): String? = try {
    suspendCancellableCoroutine { cont ->
        FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
            if (!cont.isActive) return@addOnCompleteListener
            if (task.isSuccessful) {
                cont.resume(task.result)
            } else {
                Log.w(PUSH_LOG_TAG, "fcm: token alınamadı", task.exception)
                cont.resume(null)
            }
        }
    }
} catch (e: CancellationException) {
    throw e
} catch (e: Exception) {
    // `getInstance()` itself throws when FirebaseApp never initialised (google-services.json
    // not processed) or Play Services is absent — an emulator image without GMS, say.
    Log.w(PUSH_LOG_TAG, "fcm: FirebaseMessaging kullanılamıyor", e)
    null
}
