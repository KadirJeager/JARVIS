package com.jarvis

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager

/**
 * Presence holder for a live voice call — owns NO call logic. The session object lives
 * in [com.jarvis.ui.voicecall.VoiceCallViewModel]; this service exists so the process
 * keeps its right to the microphone and the CPU while a call is live with the screen
 * off or another app in front. Without it, the first screen timeout killed the call
 * ("Software caused connection abort", saha 26 Tem 2026).
 *
 * Started when a call leaves IDLE, stopped when it returns to IDLE/ERROR — both from
 * MainActivity, which is the only place that observes the call phase with a Context in
 * hand. Starting is legal because the call can only begin from the foreground UI
 * (targetSdk 36 while-in-use rule for microphone-type services).
 */
class VoiceCallService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                "Canlı konuşma",
                // LOW: silent, no heads-up — the user is IN the call, no need to ping them.
                NotificationManager.IMPORTANCE_LOW,
            ),
        )
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.logo_kj)
            .setContentTitle("Jarvis ile canlı konuşma")
            .setContentText("Mikrofon açık — konuşma sürüyor")
            .setOngoing(true)
            .build()
        startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)

        // Audio callbacks mostly keep the CPU up on their own; the partial wake lock
        // closes the doze gap between mic frames on aggressive OEM power management.
        if (wakeLock == null) {
            wakeLock = (getSystemService(Context.POWER_SERVICE) as PowerManager)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "jarvis:voice-call")
                .apply { acquire(MAX_CALL_MS) }
        }
        return START_NOT_STICKY // a killed process has no call to resume — do not resurrect
    }

    override fun onDestroy() {
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val CHANNEL_ID = "voice_call"
        private const val NOTIFICATION_ID = 42
        /** Wake-lock safety ceiling; matches the server's 1h WebSocket request timeout. */
        private const val MAX_CALL_MS = 60L * 60 * 1000

        fun start(context: Context) {
            context.startForegroundService(Intent(context, VoiceCallService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, VoiceCallService::class.java))
        }
    }
}
