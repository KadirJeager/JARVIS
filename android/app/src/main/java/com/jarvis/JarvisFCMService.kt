package com.jarvis

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.jarvis.data.push.PUSH_LOG_TAG
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.runBlocking

/**
 * The Android edge of the push path — deliberately a SHELL.
 *
 * Every decision about whether and what to register lives in
 * [com.jarvis.data.push.FcmTokenRegistrar], which knows no Android types and is therefore
 * pinned by a plain JVM test. What is left here is the part only the framework can do:
 * receive the callback, build a notification, hand off.
 *
 * Named to match `brain/app/main.py::FcmRegisterRequest`'s docstring
 * ("JarvisFCMService.onNewToken") — one concept, one name, across both halves.
 */
class JarvisFCMService : FirebaseMessagingService() {

    /**
     * The legacy token callback, reached via the `…messaging.NEW_TOKEN` intent action.
     *
     * Deprecated in firebase-messaging 25.x in favour of [onRegistered] — and BOTH are
     * overridden on purpose. `FirebaseMessagingService.handleIntent` dispatches to one or
     * the other by intent action (`NEW_TOKEN` vs `FCM_REGISTERED`), decided at runtime by
     * the installed Play Services registration path, not by anything in this build. A
     * service that overrode only one would, on a device that takes the other branch,
     * silently never learn its token — which is the exact class of failure this slice
     * exists to end. Overriding both costs one delegating line.
     */
    @Deprecated("Firebase 25.x renamed this to onRegistered; kept because the SDK still dispatches to it.")
    @Suppress("OVERRIDE_DEPRECATION")
    override fun onNewToken(token: String) = handleToken("onNewToken", token)

    /** The current name for the same event (`…messaging.FCM_REGISTERED` intent action). */
    override fun onRegistered(token: String) = handleToken("onRegistered", token)

    /**
     * Firebase calls the token callbacks on ITS OWN background thread and keeps the
     * service alive until they return, so blocking here is both legal and necessary: a
     * fire-and-forget coroutine could be cut off mid-request when the service stops, and a
     * token that was only half-registered is exactly the failure this slice removes.
     *
     * [source] is in the log line because which of the two callbacks actually fires on
     * Kadir's device is not knowable from this side — one field log answers it.
     */
    private fun handleToken(source: String, token: String) {
        val registrar = (application as? JarvisApp)?.container?.fcmTokenRegistrar
        if (registrar == null) {
            Log.w(PUSH_LOG_TAG, "fcm: $source geldi ama container hazır değil")
            return
        }
        val outcome = runBlocking { registrar.register(token) }
        Log.i(PUSH_LOG_TAG, "fcm: $source sonuç=$outcome tokenLen=${token.length}")
    }

    /**
     * Called only while the app is in the FOREGROUND. Backgrounded, the system tray draws
     * the `notification` block itself and copies `data` into the launch intent's extras —
     * which is why [EXTRA_APPROVAL_ID] equals the wire key `approval_id` exactly, and why
     * the channel is created at app start ([JarvisApp]) rather than only here: on that
     * path this method never runs.
     */
    override fun onMessageReceived(message: RemoteMessage) {
        val approvalId = message.data[EXTRA_APPROVAL_ID]?.takeIf { it.isNotBlank() }
        // `notification` is what fcm.dispatch always sends; the `data` fallback keeps a
        // data-only push (should one ever be added server-side) from arriving silent.
        val title = message.notification?.title ?: message.data["title"] ?: "Jarvis"
        val body = message.notification?.body ?: message.data["body"].orEmpty()

        val manager = getSystemService(NotificationManager::class.java)
        // DATA-level: a push that arrived and was then dropped by a denied POST_NOTIFICATIONS
        // looks identical to a push that never arrived, unless this line says otherwise.
        // No permission dialog is raised from here — the voice-call flow in MainActivity
        // already asks for POST_NOTIFICATIONS, and a second competing prompt would be worse
        // than a missed heads-up.
        Log.i(
            PUSH_LOG_TAG,
            "fcm: mesaj alındı approvalId=$approvalId bildirimAçık=${manager.areNotificationsEnabled()}",
        )

        ensureChannel(this)
        manager.notify(notificationId(approvalId), buildNotification(title, body, approvalId))
    }

    private fun buildNotification(title: String, body: String, approvalId: String?): Notification {
        val intent = Intent(this, MainActivity::class.java).apply {
            // MainActivity has the default launch mode, so CLEAR_TOP finishes and RECREATES
            // it — which is the point: the approval id is read in `onCreate`'s
            // LaunchedEffect, and a warm Activity resumed without a restart would never
            // look at these extras at all.
            addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK)
            if (approvalId != null) putExtra(EXTRA_APPROVAL_ID, approvalId)
        }
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.logo_kj)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(
                PendingIntent.getActivity(
                    this,
                    // A per-approval request code AND FLAG_UPDATE_CURRENT: extras are not
                    // part of PendingIntent equality, so sharing one request code would
                    // hand every later notification the FIRST approval's id.
                    notificationId(approvalId),
                    intent,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                ),
            )
            .build()
    }

    /**
     * Stable per approval, fresh otherwise. Re-pushing the same approval REPLACES its
     * notification instead of stacking a second copy of one decision; a reminder, which
     * carries no approval id, gets its own slot so two reminders never overwrite each
     * other.
     */
    private fun notificationId(approvalId: String?): Int =
        approvalId?.hashCode() ?: nextNotificationId.incrementAndGet()

    companion object {
        /** Also declared as `default_notification_channel_id` in the manifest, for the
         * background/system-tray path that never reaches this class. */
        const val CHANNEL_ID = "jarvis_push"

        private val nextNotificationId = AtomicInteger(1_000)

        /**
         * Idempotent (`createNotificationChannel` overwrites by id), so both callers may
         * run it: [JarvisApp.onCreate] covers the system-tray path, [onMessageReceived]
         * covers a process that Firebase started without the Application ever having drawn
         * anything.
         *
         * IMPORTANCE_HIGH on purpose: North Star §4.8 puts red-zone approvals on the
         * critical path, and an approval the user does not SEE has the same effect as one
         * that was never sent.
         */
        fun ensureChannel(context: Context) {
            context.getSystemService(NotificationManager::class.java).createNotificationChannel(
                NotificationChannel(
                    CHANNEL_ID,
                    "Onaylar ve hatırlatmalar",
                    NotificationManager.IMPORTANCE_HIGH,
                ),
            )
        }
    }
}
