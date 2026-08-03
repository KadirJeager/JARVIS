package com.jarvis

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log

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
        // Tapping the notification (screen was off, user wants back in) reopens the app.
        val contentIntent = android.app.PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            android.app.PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.logo_kj)
            .setContentTitle("Jarvis ile canlı konuşma")
            .setContentText("Mikrofon açık — konuşma sürüyor")
            .setContentIntent(contentIntent)
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
        routeVoiceToSpeaker()
        return START_NOT_STICKY // a killed process has no call to resume — do not resurrect
    }

    /**
     * The reply plays as USAGE_VOICE_COMMUNICATION (AndroidTextToSpeech — the AEC fix),
     * and on a phone that usage routes to the EARPIECE by default. Nobody holds Jarvis
     * to their ear: since the AEC fix shipped, replies were "silent" — playing quietly
     * out of the earpiece (4 Ağu 02:12). A live call therefore routes communication
     * audio to the loudspeaker, the way every VoIP app's speakerphone does.
     *
     * Honoured: a WIRED headset (plugging a cable in is a deliberate choice) and a
     * BLE headset (TYPE_BLE_HEADSET is what modern earbuds register as — "kulaklık
     * takılıysa ses oradan gelsin", 4 Ağu). Overridden: classic Bluetooth SCO — the
     * first field test (02:22) found the Galaxy Watch registered EXACTLY as that
     * (`Kadir(7)`) and the replies played on the WRIST while the phone stayed silent.
     * An SCO-only legacy headphone loses to the speaker under this rule; the log line
     * names every bypassed device so that day is a one-line diagnosis.
     */
    private fun routeVoiceToSpeaker() {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val devices = audio.availableCommunicationDevices
            val headset = devices.any {
                it.type == AudioDeviceInfo.TYPE_WIRED_HEADSET ||
                    it.type == AudioDeviceInfo.TYPE_WIRED_HEADPHONES ||
                    it.type == AudioDeviceInfo.TYPE_USB_HEADSET ||
                    it.type == AudioDeviceInfo.TYPE_BLE_HEADSET
            }
            if (headset) {
                Log.i(TAG, "voice: kulaklık bağlı, yönlendirmeye dokunulmadı")
                return
            }
            val bypassed = devices.filter {
                it.type != AudioDeviceInfo.TYPE_BUILTIN_SPEAKER &&
                    it.type != AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            }.joinToString { "${it.productName}(${it.type})" }
            val speaker = devices.firstOrNull { it.type == AudioDeviceInfo.TYPE_BUILTIN_SPEAKER }
            val ok = speaker != null && audio.setCommunicationDevice(speaker)
            // DATA-level: a silent reply and a mis-routed reply look identical to the
            // user; this line is what tells them apart in logcat.
            Log.i(TAG, "voice: hoparlöre yönlendirildi=$ok ezilen=[$bypassed]")
        } else {
            @Suppress("DEPRECATION")
            audio.isSpeakerphoneOn = true
        }
    }

    private fun clearVoiceRoute() {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audio.clearCommunicationDevice()
        } else {
            @Suppress("DEPRECATION")
            audio.isSpeakerphoneOn = false
        }
    }

    /**
     * The user swiping the task away destroys the Activity (ViewModel.onCleared ends the
     * call correctly) — but nothing recomposes to call stop(), so without this the
     * service would idle on for up to MAX_CALL_MS holding a wake lock and a stale
     * "microphone on" notification (review Important #2).
     */
    override fun onTaskRemoved(rootIntent: Intent?) {
        stopSelf()
    }

    override fun onDestroy() {
        clearVoiceRoute()
        wakeLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val TAG = "VoiceCallService"
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
