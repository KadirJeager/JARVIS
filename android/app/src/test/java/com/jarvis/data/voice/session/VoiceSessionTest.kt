package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.TranscriptLine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * [VoiceSession] is the frame-routing state machine described in the Katman 3b spec: it
 * owns no Android type directly (audio and the socket are behind [MicSource]/[SpeakerSink]/
 * [VoiceTransport]), so every transition here is driven through fakes and is fully
 * JVM-testable, unlike the real AudioRecord/AudioTrack/OkHttp glue.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class VoiceSessionTest {

    private val voiceUrl = "wss://jarvis-voice.example/ws/voice"

    private class FakeVoiceTransport : VoiceTransport {
        var connectCalls = 0
        var connectedUrl: String? = null
        var listener: VoiceTransportListener? = null
        val sentTexts = mutableListOf<String>()
        val sentBinaries = mutableListOf<ByteArray>()
        var closeCalls = 0

        override fun connect(url: String, listener: VoiceTransportListener) {
            connectCalls++
            connectedUrl = url
            this.listener = listener
        }

        override fun sendText(text: String): Boolean {
            sentTexts += text
            return true
        }

        override fun sendBinary(bytes: ByteArray): Boolean {
            sentBinaries += bytes
            return true
        }

        override fun close() {
            closeCalls++
        }
    }

    private class FakeMicSource : MicSource {
        var startedRate: Int? = null
        var stopCalls = 0
        val frames = mutableListOf<ByteArray>()

        override fun start(sampleRateHz: Int) {
            startedRate = sampleRateHz
        }

        override suspend fun readFrame(): ByteArray? =
            if (frames.isNotEmpty()) frames.removeAt(0) else null

        override fun stop() {
            stopCalls++
        }
    }

    private class FakeSpeakerSink : SpeakerSink {
        var startedRate: Int? = null
        val written = mutableListOf<ByteArray>()
        var stopCalls = 0

        override fun start(sampleRateHz: Int) {
            startedRate = sampleRateHz
        }

        override fun write(pcm: ByteArray) {
            written += pcm
        }

        override fun stop() {
            stopCalls++
        }
    }

    private class Fixture(scope: CoroutineScope, token: String? = "tok-xyz") {
        val transport = FakeVoiceTransport()
        val mic = FakeMicSource()
        val speaker = FakeSpeakerSink()
        val session = VoiceSession(
            transport = transport,
            mic = mic,
            speaker = speaker,
            tokenProvider = { token },
            deviceHint = "android-phone",
            scope = scope,
            voiceUrl = "wss://jarvis-voice.example/ws/voice",
        )
    }

    // -- start() / connection lifecycle -------------------------------------------------

    @Test
    fun start_movesToConnecting_andConnectsTheGivenUrl() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        assertEquals(VoicePhase.CONNECTING, f.session.state.value.phase)
        assertEquals(1, f.transport.connectCalls)
        assertEquals(voiceUrl, f.transport.connectedUrl)
    }

    @Test
    fun start_withNoToken_reportsTurkishError_andNeverConnects() = runTest {
        val f = Fixture(backgroundScope, token = null)
        f.session.start()
        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertNotNull(f.session.state.value.errorMessage)
        assertEquals(0, f.transport.connectCalls)
    }

    @Test
    fun start_whileAlreadyActive_isIgnored() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.session.start()
        assertEquals(1, f.transport.connectCalls)
    }

    @Test
    fun onOpen_sendsHelloFrame_withTokenAndDeviceHint() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        assertEquals(1, f.transport.sentTexts.size)
        val hello = Json.parseToJsonElement(f.transport.sentTexts[0]).jsonObject
        assertEquals("tok-xyz", hello.getValue("token").jsonPrimitive.content)
        assertEquals("android-phone", hello.getValue("device_hint").jsonPrimitive.content)
        assertEquals("foreground", hello.getValue("presence").jsonPrimitive.content)
    }

    @Test
    fun onOpen_movesToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    /**
     * Mutation-check target #1: swapping which constant feeds which fake here (16000 <->
     * 24000) must fail this test. Literal numbers on purpose, not the AUDIO_*_RATE_HZ
     * constants -- so a mutation of the constants themselves would ALSO be caught, not
     * just a self-consistent swap of both.
     */
    @Test
    fun onOpen_startsCaptureAt16000Hz_andPlaybackAt24000Hz() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        assertEquals(16000, f.mic.startedRate)
        assertEquals(24000, f.speaker.startedRate)
    }

    // -- teardown races (review Critical #1, 26 Tem 2026) --------------------------------

    /**
     * The one interleaving the generation guard alone cannot stop: onOpen (OkHttp reader
     * thread) passes the gen check, then stop() (UI thread) completes a FULL teardown,
     * then onOpen resumes — starting the mic AFTER teardown with nothing left to stop it,
     * and resurrecting the state out of IDLE. The session must serialize callback bodies
     * against endSession so this window does not exist.
     */
    @Test(timeout = 10_000)
    fun stop_racingOnOpen_neverLeavesTheMicRunning_orResurrectsState() {
        val scope = CoroutineScope(UnconfinedTestDispatcher())
        val transport = FakeVoiceTransport()
        val speaker = FakeSpeakerSink()

        val micEntered = java.util.concurrent.CountDownLatch(1)
        val micRelease = java.util.concurrent.CountDownLatch(1)
        val callLog = java.util.Collections.synchronizedList(mutableListOf<String>())
        val mic = object : MicSource {
            override fun start(sampleRateHz: Int) {
                callLog.add("start")
                micEntered.countDown()
                micRelease.await() // hold onOpen mid-body while stop() races it
            }
            override suspend fun readFrame(): ByteArray? = null
            override fun stop() {
                callLog.add("stop")
            }
        }
        val session = VoiceSession(
            transport = transport,
            mic = mic,
            speaker = speaker,
            tokenProvider = { "tok" },
            deviceHint = "test",
            scope = scope,
            voiceUrl = voiceUrl,
        )

        session.start()
        val opener = Thread { transport.listener!!.onOpen() }
        opener.start()
        assertTrue(micEntered.await(5, java.util.concurrent.TimeUnit.SECONDS))

        val stopper = Thread { session.stop() }
        stopper.start()
        // Give stop() time to reach the contended section, then let onOpen finish.
        Thread.sleep(200)
        micRelease.countDown()
        opener.join(5_000)
        stopper.join(5_000)

        // Whatever the interleaving, the LAST word on the microphone must be "stop",
        // and the call must end IDLE — not resurrect to LISTENING.
        assertEquals("stop", callLog.last())
        assertEquals(VoicePhase.IDLE, session.state.value.phase)
    }

    /**
     * Review Important #3: AudioRecord/AudioTrack construction can throw (mic held by a
     * real phone call, device init failure) — and it throws INSIDE onOpen on OkHttp's
     * reader thread. Uncaught, that kills the process. It must instead end the session
     * with a Turkish error the overlay can show.
     */
    @Test
    fun micFailingToOpen_endsTheCallWithError_insteadOfCrashing() = runTest {
        val transport = FakeVoiceTransport()
        val speaker = FakeSpeakerSink()
        val mic = object : MicSource {
            override fun start(sampleRateHz: Int) = throw IllegalStateException("startRecording() called on an uninitialized AudioRecord")
            override suspend fun readFrame(): ByteArray? = null
            override fun stop() {}
        }
        val session = VoiceSession(
            transport = transport,
            mic = mic,
            speaker = speaker,
            tokenProvider = { "tok" },
            deviceHint = "test",
            scope = backgroundScope,
            voiceUrl = voiceUrl,
        )
        session.start()
        transport.listener!!.onOpen() // must not throw out of the callback

        assertEquals(VoicePhase.ERROR, session.state.value.phase)
        assertEquals("Mikrofon veya hoparlör açılamadı. Aramayı yeniden başlatmayı dene.", session.state.value.errorMessage)
        assertEquals(1, transport.closeCalls)
    }

    // -- server -> client events ----------------------------------------------------------

    /**
     * Saha bulgusu (26 Tem 2026): the model's output transcription arrives word by word
     * ("Merhaba", "Kadir!", "Sesini"...), and appending each fragment as its own line
     * rendered ONE BUBBLE PER WORD. Consecutive fragments from the same role must merge
     * into one line; a role change starts a new line.
     */
    @Test
    fun transcriptFragments_fromSameRole_mergeIntoOneLine() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"transcript","role":"jarvis","text":"Merhaba"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"jarvis","text":"Kadir!"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"selam"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"jarvis"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"jarvis","text":"Buyur"}""")

        val lines = f.session.state.value.transcript
        assertEquals(3, lines.size)
        assertEquals("jarvis" to "Merhaba Kadir!", lines[0].role to lines[0].text)
        assertEquals("user" to "selam jarvis", lines[1].role to lines[1].text)
        assertEquals("jarvis" to "Buyur", lines[2].role to lines[2].text)
    }

    /** Review Minor #8: two consecutive USER turns are separate utterances — a
     *  turn_complete between same-role fragments must break the merge. */
    @Test
    fun turnComplete_breaksTheMerge_betweenSameRoleTurns() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"saat kaç"}""")
        f.transport.listener!!.onText("""{"type":"turn_complete"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"hava nasıl"}""")

        val lines = f.session.state.value.transcript
        assertEquals(2, lines.size)
        assertEquals("saat kaç", lines[0].text)
        assertEquals("hava nasıl", lines[1].text)
    }

    @Test
    fun transcriptEvents_accumulate_inOrder_bothRoles() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"merhaba"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"model","text":"selam"}""")

        assertEquals(
            listOf(TranscriptLine("user", "merhaba"), TranscriptLine("model", "selam")),
            f.session.state.value.transcript,
        )
    }

    @Test
    fun binaryFrame_isWrittenToSpeaker_andMovesToSpeaking() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        val chunk = byteArrayOf(1, 2, 3, 4)
        f.transport.listener!!.onBinary(chunk)

        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
        assertEquals(1, f.speaker.written.size)
        assertTrue(chunk.contentEquals(f.speaker.written[0]))
    }

    /**
     * Mutation-check target #2: making the session ignore `turn_complete` must fail this
     * test -- phase would stay SPEAKING forever instead of reverting to LISTENING.
     */
    @Test
    fun turnComplete_revertsFromSpeakingBackToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onBinary(byteArrayOf(9))
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.transport.listener!!.onText("""{"type":"turn_complete"}""")
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    @Test
    fun speakerEvent_updatesLastSpeakerVerified() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        assertNull(f.session.state.value.lastSpeakerVerified)

        f.transport.listener!!.onText("""{"type":"speaker","role":"user","verified":true,"score":0.83}""")
        assertEquals(true, f.session.state.value.lastSpeakerVerified)

        f.transport.listener!!.onText("""{"type":"speaker","role":"user","verified":false,"score":0.1}""")
        assertEquals(false, f.session.state.value.lastSpeakerVerified)
    }

    @Test
    fun errorEvent_endsTheSession_withTheServersMessage() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"error","message":"model kullanilamiyor"}""")

        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertTrue(f.session.state.value.errorMessage!!.contains("model kullanilamiyor"))
        assertEquals(1, f.mic.stopCalls)
        assertEquals(1, f.speaker.stopCalls)
        assertEquals(1, f.transport.closeCalls)
    }

    @Test
    fun unknownOrMalformedTextFrame_isIgnored_sessionStaysListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"future_event","payload":1}""")
        f.transport.listener!!.onText("not json at all {{{")

        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        assertTrue(f.session.state.value.transcript.isEmpty())
        assertNull(f.session.state.value.errorMessage)
    }

    @Test
    fun transportFailure_setsErrorPhase_andStopsAudio() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onFailure("soket koptu")

        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertTrue(f.session.state.value.errorMessage!!.contains("soket koptu"))
        assertEquals(1, f.mic.stopCalls)
        assertEquals(1, f.speaker.stopCalls)
    }

    @Test
    fun transportClosed_withoutAPriorError_returnsToIdle() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onClosed()

        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertNull(f.session.state.value.errorMessage)
    }

    // -- mic capture loop -----------------------------------------------------------------

    @Test
    fun micFrames_areForwardedAsBinary_toTheTransport() = runTest {
        // UnconfinedTestDispatcher, not backgroundScope: the fake's readFrame() never
        // truly suspends, so an Unconfined launch runs the whole read/send loop eagerly
        // and synchronously inside onOpen() -- no scheduler-advance ambiguity.
        val loopScope = CoroutineScope(UnconfinedTestDispatcher())
        val f = Fixture(loopScope)
        f.mic.frames += byteArrayOf(1, 2, 3)
        f.mic.frames += byteArrayOf(4, 5, 6)

        f.session.start()
        f.transport.listener!!.onOpen()
        advanceUntilIdle()

        assertEquals(2, f.transport.sentBinaries.size)
        assertTrue(byteArrayOf(1, 2, 3).contentEquals(f.transport.sentBinaries[0]))
        assertTrue(byteArrayOf(4, 5, 6).contentEquals(f.transport.sentBinaries[1]))
    }

    // -- stop() / teardown ordering ---------------------------------------------------------

    @Test
    fun stop_whileIdle_isANoOp() = runTest {
        val f = Fixture(backgroundScope)
        f.session.stop()
        assertEquals(0, f.transport.closeCalls)
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
    }

    @Test
    fun stop_whileConnecting_closesTransport_andReturnsToIdle() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.session.stop()

        assertEquals(1, f.transport.closeCalls)
        assertEquals(1, f.mic.stopCalls)
        assertEquals(1, f.speaker.stopCalls)
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertNull(f.session.state.value.errorMessage)
    }

    @Test
    fun stop_whileListening_stopsAudio_andClosesTransport() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.session.stop()

        assertEquals(1, f.mic.stopCalls)
        assertEquals(1, f.speaker.stopCalls)
        assertEquals(1, f.transport.closeCalls)
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
    }

    /**
     * Mirrors app.js's `voiceGen` guard: a callback from a socket that belonged to a
     * PREVIOUS start()/stop() cycle must not clobber the state of whatever is happening
     * now. Without this guard, a late `onBinary` from an already-stopped socket would
     * silently resurrect SPEAKING after the user explicitly stopped voice mode.
     */
    @Test
    fun lateCallbackFromAStoppedGeneration_isIgnored() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        val staleListener = f.transport.listener!!

        f.session.stop()
        staleListener.onBinary(byteArrayOf(1))
        staleListener.onText("""{"type":"transcript","role":"model","text":"gec gelen"}""")

        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertTrue(f.session.state.value.transcript.isEmpty())
        assertTrue(f.speaker.written.isEmpty())
    }

    // -- reportError() (used for permission-denied, before any socket exists) -------------

    @Test
    fun reportError_whileIdle_setsErrorPhase() = runTest {
        val f = Fixture(backgroundScope)
        f.session.reportError("Mikrofon izni verilmedi.")
        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertEquals("Mikrofon izni verilmedi.", f.session.state.value.errorMessage)
    }

    @Test
    fun reportError_whileASessionIsActive_isIgnored() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.session.reportError("izin reddedildi")
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    @Test
    fun stop_afterAnError_clearsIt_backToIdle() = runTest {
        val f = Fixture(backgroundScope)
        f.session.reportError("gecici hata")
        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        f.session.stop()
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertNull(f.session.state.value.errorMessage)
    }
}
