package com.jarvis.ui.voicecall

import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.viewmodel.CreationExtras
import com.jarvis.data.voice.session.MicSource
import com.jarvis.data.voice.session.SpeakerSink
import com.jarvis.data.voice.session.VoicePhase
import com.jarvis.data.voice.session.VoiceSession
import com.jarvis.data.voice.session.VoiceTransport
import com.jarvis.data.voice.session.VoiceTransportListener
import kotlin.reflect.KClass
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The ViewModel is a thin lifecycle adapter over [VoiceSession]; what it OWNS — and what
 * these tests pin — is that leaving the screen can never leak a live microphone: clearing
 * the ViewModel must end the call, and a permission denial must surface as a visible
 * error state rather than a silent dead button.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class VoiceCallViewModelTest {

    private class FakeTransport : VoiceTransport {
        var connectCalls = 0
        var closeCalls = 0
        var listener: VoiceTransportListener? = null

        override fun connect(url: String, listener: VoiceTransportListener) {
            connectCalls++
            this.listener = listener
        }

        override fun sendText(text: String) = true
        override fun sendBinary(bytes: ByteArray) = true
        override fun close() {
            closeCalls++
        }
    }

    private class FakeMic : MicSource {
        var stopCalls = 0
        override fun start(sampleRateHz: Int) {}
        override suspend fun readFrame(): ByteArray? = null
        override fun stop() {
            stopCalls++
        }
    }

    private class FakeSpeaker : SpeakerSink {
        var stopCalls = 0
        override fun start(sampleRateHz: Int) {}
        override fun write(pcm: ByteArray) {}
        override fun stop() {
            stopCalls++
        }
    }

    private val dispatcher = StandardTestDispatcher()
    private val transport = FakeTransport()
    private val mic = FakeMic()
    private val speaker = FakeSpeaker()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    private fun sessionFor(scope: CoroutineScope) = VoiceSession(
        transport = transport,
        mic = mic,
        speaker = speaker,
        tokenProvider = { "token" },
        deviceHint = "android_test",
        scope = scope,
        voiceUrl = "wss://voice.example/ws/voice",
    )

    /** Builds the VM through a real store so [ViewModelStore.clear] drives onCleared. */
    private fun buildInStore(): Pair<VoiceCallViewModel, ViewModelStore> {
        val store = ViewModelStore()
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : androidx.lifecycle.ViewModel> create(
                modelClass: KClass<T>,
                extras: CreationExtras,
            ): T = VoiceCallViewModel(::sessionFor) as T
        }
        val vm = ViewModelProvider.create(store, factory)[VoiceCallViewModel::class]
        return vm to store
    }

    /**
     * Saha (26 Tem 03:30): the hello frame has no 401-refresh-retry path like HTTP does,
     * so dialing with the cached (possibly hour-old, dead) token failed every call with
     * "Giriş doğrulanamadı". A fresh token must be minted BEFORE the socket dials.
     */
    @Test
    fun start_refreshesAuth_beforeDialing() {
        val order = mutableListOf<String>()
        val store = ViewModelStore()
        val factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : androidx.lifecycle.ViewModel> create(
                modelClass: KClass<T>,
                extras: CreationExtras,
            ): T = VoiceCallViewModel(::sessionFor, refreshAuth = { order.add("refresh") }) as T
        }
        val vm = ViewModelProvider.create(store, factory)[VoiceCallViewModel::class]

        vm.start()
        dispatcher.scheduler.advanceUntilIdle()

        assertEquals(1, transport.connectCalls)
        assertEquals(listOf("refresh"), order)
        assertEquals(VoicePhase.CONNECTING, vm.state.value.phase)
    }

    @Test
    fun start_opensTheSession() {
        val (vm, _) = buildInStore()
        vm.start()
        dispatcher.scheduler.advanceUntilIdle()
        assertEquals(1, transport.connectCalls)
        assertEquals(VoicePhase.CONNECTING, vm.state.value.phase)
    }

    @Test
    fun micPermissionDenied_surfacesErrorState() {
        val (vm, _) = buildInStore()
        vm.onMicPermissionDenied()
        assertEquals(VoicePhase.ERROR, vm.state.value.phase)
        assertNotNull(vm.state.value.errorMessage)
    }

    @Test
    fun clearingTheViewModel_endsALiveCall() {
        val (vm, store) = buildInStore()
        vm.start()
        dispatcher.scheduler.advanceUntilIdle()
        transport.listener!!.onOpen()
        assertEquals(VoicePhase.LISTENING, vm.state.value.phase)

        store.clear()

        assertEquals(1, transport.closeCalls)
        assertEquals(1, mic.stopCalls)
        assertEquals(1, speaker.stopCalls)
    }

    @Test
    fun stop_returnsToIdle() {
        val (vm, _) = buildInStore()
        vm.start()
        dispatcher.scheduler.advanceUntilIdle()
        transport.listener!!.onOpen()
        vm.stop()
        assertEquals(VoicePhase.IDLE, vm.state.value.phase)
        assertTrue(transport.closeCalls >= 1)
    }
}
