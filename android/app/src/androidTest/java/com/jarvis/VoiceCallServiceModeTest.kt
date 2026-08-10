package com.jarvis

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.rule.GrantPermissionRule
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Pins the actual platform call that wakes the echo-cancellation chain: AudioRecord already
 * uses VOICE_COMMUNICATION and TTS already uses USAGE_VOICE_COMMUNICATION, but neither of those
 * puts the device into a communication routing state on its own -- only AudioManager.setMode
 * does.
 *
 * Deviation from the task brief: the brief's test used androidx.test.rule.ServiceTestRule,
 * whose startService() binds to the service and blocks on onServiceConnected. VoiceCallService
 * .onBind() intentionally returns null (see its class doc -- it is a pure started-service
 * presence holder, nothing ever binds to it), so on API 28+ the platform calls
 * ServiceConnection.onNullBinding() instead of onServiceConnected(). ServiceTestRule's internal
 * ProxyServiceConnection does not override onNullBinding(), so the connect latch never counts
 * down and startService() always throws TimeoutException after 5s -- independent of whether
 * MODE_IN_COMMUNICATION is ever set. Verified by decompiling the pinned rules-1.6.1.aar and by
 * running the brief's literal test against emulator-5554 (API 36): it failed with
 * TimeoutException at ServiceTestRule.waitOnLatch, never reaching the mode assertion. This test
 * instead starts the service the way production code does (Context.startService) and polls
 * audio.mode -- the same wait-for-async-service-state idiom VoiceCallWiringTest already uses
 * for this exact service.
 *
 * RECORD_AUDIO + POST_NOTIFICATIONS are granted by rule -- without them startForeground with
 * FOREGROUND_SERVICE_TYPE_MICROPHONE throws and the whole class fails for the wrong reason.
 */
@RunWith(AndroidJUnit4::class)
class VoiceCallServiceModeTest {

    @get:Rule
    val permissions: GrantPermissionRule = GrantPermissionRule.grant(
        android.Manifest.permission.RECORD_AUDIO,
        android.Manifest.permission.POST_NOTIFICATIONS,
    )

    private fun waitUntil(timeoutMs: Long = 5_000, cond: () -> Boolean): Boolean {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (cond()) return true
            Thread.sleep(50)
        }
        return cond()
    }

    @Test
    fun serviceSetsCommunicationModeAndRestoresIt() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager

        context.startService(Intent(context, VoiceCallService::class.java))
        assertTrue(
            "audio mode never switched to MODE_IN_COMMUNICATION",
            waitUntil { audio.mode == AudioManager.MODE_IN_COMMUNICATION },
        )

        context.stopService(Intent(context, VoiceCallService::class.java))
        assertTrue(
            "audio mode was not restored after teardown",
            waitUntil { audio.mode == AudioManager.MODE_NORMAL },
        )
    }
}
