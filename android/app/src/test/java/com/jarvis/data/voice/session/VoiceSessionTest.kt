package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.TranscriptLine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
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
 * [VoiceSession] is the frame-routing state machine of protocol v2: it owns no Android
 * type directly (audio and the socket are behind [MicSource]/[SpeechToText]/
 * [SpeechSynthesis]/[VoiceTransport]), so every transition here is driven through fakes
 * and is fully JVM-testable, unlike the real AudioRecord/SpeechRecognizer/TextToSpeech/
 * OkHttp glue.
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

    private class FakeSpeechToText : SpeechToText {
        var startCalls = 0
        var listenCalls = 0
        var destroyCalls = 0
        var listener: SpeechToTextListener? = null

        override fun start(listener: SpeechToTextListener) {
            startCalls++
            this.listener = listener
        }

        override fun listen() {
            listenCalls++
        }

        override fun destroy() {
            destroyCalls++
        }
    }

    private class FakeSpeechSynthesis : SpeechSynthesis {
        var startCalls = 0
        val spoken = mutableListOf<String>()
        var stopCalls = 0
        var destroyCalls = 0
        var listener: SpeechSynthesisListener? = null

        override fun start(listener: SpeechSynthesisListener) {
            startCalls++
            this.listener = listener
        }

        override fun speak(text: String) {
            spoken += text
        }

        override fun stop() {
            stopCalls++
        }

        override fun destroy() {
            destroyCalls++
        }
    }

    /** Movable wall clock for the echo guard's deadlines. The guard is a DEADLINE, so
     *  it needs a clock the test can push forward independently of virtual coroutine
     *  time (the two are unrelated: the guard never sleeps, it compares timestamps). */
    private class FakeClock(var now: Long = 1_000_000L) : () -> Long {
        override fun invoke(): Long = now
    }

    /** A mic that SUSPENDS when it has nothing rather than ending the capture loop, so
     *  one test can span several guard states. [FakeMicSource] returns null on empty,
     *  which breaks the loop for good and cannot express "and then, later, more audio". */
    private class ChannelMicSource : MicSource {
        var startedRate: Int? = null
        var stopCalls = 0

        /** How many frames the capture loop actually pulled. Without this an
         *  "assertEquals(0, sentBinaries.size)" would also pass when the loop never ran
         *  at all -- a vacuous green. Every drop assertion pairs the two. */
        var readCount = 0
        private val channel = Channel<ByteArray>(Channel.UNLIMITED)

        fun emit(frame: ByteArray) {
            channel.trySend(frame)
        }

        override fun start(sampleRateHz: Int) {
            startedRate = sampleRateHz
        }

        override suspend fun readFrame(): ByteArray? {
            val frame = channel.receive()
            readCount++
            return frame
        }

        override fun stop() {
            stopCalls++
        }
    }

    private class Fixture(
        scope: CoroutineScope,
        token: String? = "tok-xyz",
        val clock: FakeClock = FakeClock(),
        val mic: MicSource = FakeMicSource(),
    ) {
        val transport = FakeVoiceTransport()
        val stt = FakeSpeechToText()
        val tts = FakeSpeechSynthesis()

        /** Collecting logger for the timeline-instrumentation tests: every line the
         *  session would hand to `Log.i` in production lands here instead, in order. */
        val lines = mutableListOf<String>()

        val session = VoiceSession(
            transport = transport,
            mic = mic,
            stt = stt,
            tts = tts,
            tokenProvider = { token },
            deviceHint = "android-phone",
            scope = scope,
            voiceUrl = "wss://jarvis-voice.example/ws/voice",
            nowMs = clock,
            logger = { line -> lines += line },
        )

        /** Most tests still use the null-terminating fake; this keeps them readable. */
        val fakeMic: FakeMicSource get() = mic as FakeMicSource
    }

    private fun lastSentTextJson(transport: FakeVoiceTransport) =
        Json.parseToJsonElement(transport.sentTexts.last()).jsonObject

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
    fun onOpen_sendsHelloFrame_withTokenDeviceHintAndProtocolV2Caps() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        assertEquals(1, f.transport.sentTexts.size)
        val hello = Json.parseToJsonElement(f.transport.sentTexts[0]).jsonObject
        assertEquals("tok-xyz", hello.getValue("token").jsonPrimitive.content)
        assertEquals("android-phone", hello.getValue("device_hint").jsonPrimitive.content)
        assertEquals("foreground", hello.getValue("presence").jsonPrimitive.content)
        // Protocol v2: the server rejects a caps-less hello, so the session's first
        // frame must declare device-side STT/TTS, not leave it to the builder default.
        val caps = hello.getValue("client_caps").jsonObject
        assertEquals("device", caps.getValue("stt").jsonPrimitive.content)
        assertEquals("device", caps.getValue("tts").jsonPrimitive.content)
        assertEquals(2, caps.getValue("proto").jsonPrimitive.content.toInt())
    }

    @Test
    fun onOpen_movesToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    /**
     * Mutation-check target #1: the literal 16000 on purpose, not the AUDIO_IN_RATE_HZ
     * constant -- a mutation of the constant itself must ALSO be caught. And onOpen must
     * arm the full device-side pipeline: STT started and listening, TTS initialized.
     */
    @Test
    fun onOpen_startsCaptureAt16000Hz_andArmsSttAndTts() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        assertEquals(16000, f.fakeMic.startedRate)
        assertEquals(1, f.stt.startCalls)
        assertEquals(1, f.stt.listenCalls)
        assertEquals(1, f.tts.startCalls)
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
        val stt = FakeSpeechToText()
        val tts = FakeSpeechSynthesis()

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
            stt = stt,
            tts = tts,
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
     * Review Important #3: AudioRecord construction can throw (mic held by a real phone
     * call) — and it throws INSIDE onOpen on OkHttp's reader thread. Uncaught, that kills
     * the process. It must instead end the session with a Turkish error the overlay can
     * show.
     */
    @Test
    fun micFailingToOpen_endsTheCallWithError_insteadOfCrashing() = runTest {
        val transport = FakeVoiceTransport()
        val stt = FakeSpeechToText()
        val tts = FakeSpeechSynthesis()
        val mic = object : MicSource {
            override fun start(sampleRateHz: Int) = throw IllegalStateException("startRecording() called on an uninitialized AudioRecord")
            override suspend fun readFrame(): ByteArray? = null
            override fun stop() {}
        }
        val session = VoiceSession(
            transport = transport,
            mic = mic,
            stt = stt,
            tts = tts,
            tokenProvider = { "tok" },
            deviceHint = "test",
            scope = backgroundScope,
            voiceUrl = voiceUrl,
        )
        session.start()
        transport.listener!!.onOpen() // must not throw out of the callback

        assertEquals(VoicePhase.ERROR, session.state.value.phase)
        assertEquals("Mikrofon açılamadı. Aramayı yeniden başlatmayı dene.", session.state.value.errorMessage)
        assertEquals(1, transport.closeCalls)
    }

    // -- server -> client events ----------------------------------------------------------

    /**
     * Saha bulgusu (26 Tem 2026): streamed transcription arrives word by word
     * ("Merhaba", "Kadir!", "Sesini"...), and appending each fragment as its own line
     * rendered ONE BUBBLE PER WORD. Consecutive fragments from the same role must merge
     * into one line; a role change starts a new line.
     */
    @Test
    fun theProductionTurn_drawsTheReplyExactlyOnce() = runTest {
        // Kadir's screenshot (S23, 2026-08-03): every answer appeared TWICE inside one
        // bubble. _serve_turn sends jarvis_text ("speak this") AND transcript("jarvis")
        // ("show this") for the same reply, and the client rendered both -- merged into
        // a single line because the role and the turn boundary matched.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.serveTurn("Selam Kadir! İyiyim, teşekkürler.")

        assertEquals(
            listOf(TranscriptLine("jarvis", "Selam Kadir! İyiyim, teşekkürler.")),
            f.session.state.value.transcript,
        )
    }

    @Test
    fun transcriptFragments_fromANonJarvisRole_stillMergeIntoOneLine() = runTest {
        // The merging rule itself is unchanged -- only the duplicate jarvis echo is
        // dropped. Any other role still merges consecutive fragments into one bubble.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"selam"}""")
        f.transport.listener!!.onText("""{"type":"transcript","role":"user","text":"jarvis"}""")

        assertEquals(
            listOf(TranscriptLine("user", "selam jarvis")),
            f.session.state.value.transcript,
        )
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

    // -- jarvis_text -> on-device TTS (protocol v2) ----------------------------------------

    @Test
    fun jarvisText_isSpokenOnDevice_movesToSpeaking_appendsTranscriptLine() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Nasıl yardımcı olabilirim?"}""")

        assertEquals(listOf("Nasıl yardımcı olabilirim?"), f.tts.spoken)
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
        assertEquals(
            listOf(TranscriptLine("jarvis", "Nasıl yardımcı olabilirim?")),
            f.session.state.value.transcript,
        )
    }

    /** A reply may arrive as several jarvis_text events; like transcript fragments, the
     *  consecutive pieces of one reply must merge into ONE bubble, not N. */
    @Test
    fun jarvisTextFragments_ofOneReply_mergeIntoOneLine() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Saat"}""")
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"beş."}""")

        val lines = f.session.state.value.transcript
        assertEquals(1, lines.size)
        assertEquals("jarvis" to "Saat beş.", lines[0].role to lines[0].text)
        assertEquals(listOf("Saat", "beş."), f.tts.spoken)
    }

    /**
     * Protocol v2's replacement for the old "model audio went quiet" guard: SPEAKING is
     * driven by the device TTS, so it settles back to LISTENING when the engine reports
     * the utterance done -- with no turn_complete needed.
     */
    @Test
    fun ttsUtteranceDone_settlesSpeakingBackToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"selam"}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.tts.listener!!.onUtteranceDone()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    /** A multi-part reply: the first part finishing must NOT flicker the label back to
     *  LISTENING while the second part is still queued/speaking. */
    @Test
    fun ttsUtteranceDone_ofFirstPart_keepsSpeaking_untilLastPartDone() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"bir"}""")
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"iki"}""")

        f.tts.listener!!.onUtteranceDone()
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.tts.listener!!.onUtteranceDone()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    /**
     * Mutation-check target #2: making the session ignore `turn_complete` must fail this
     * test -- phase would stay SPEAKING instead of reverting to LISTENING.
     */
    @Test
    fun theSpokenReplyEnding_notTurnComplete_revertsSpeakingToListening() = runTest {
        // CONTRACT CHANGE (2026-08-03, echo guard): turn_complete says "no more
        // jarvis_text is coming", not "the loudspeaker went quiet" -- it arrives
        // microseconds after jarvis_text, while the device has not started speaking.
        // Settling the phase there ended the echo guard at the START of every reply.
        // The TTS engine is the only thing that knows, and it says so with
        // onUtteranceDone.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"selam"}""")
        f.transport.listener!!.onText("""{"type":"turn_complete"}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.tts.listener!!.onUtteranceDone()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    @Test
    fun aTtsThatNeverReportsDone_stillReleasesTheCall() = runTest {
        // Removing `ttsActive = 0` from turn_complete removed the only thing that
        // guaranteed SPEAKING ends. A call parked in SPEAKING is deaf AND cannot be
        // interrupted, so the watchdog is not optional.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"selam"}""")
        f.transport.listener!!.onText("""{"type":"turn_complete"}""")

        advanceTimeBy(VoiceSession.MAX_SPEAK_GUARD_MS + 100)
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
        assertEquals(1, f.fakeMic.stopCalls)
        assertEquals(1, f.stt.destroyCalls)
        assertEquals(1, f.tts.destroyCalls)
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

    /** Protocol v2: the server never sends audio down. A stray binary frame is a server
     *  bug and must be ignored -- nothing to play it with, and SPEAKING is TTS-driven. */
    @Test
    fun binaryFrameFromServer_isIgnored() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.transport.listener!!.onBinary(byteArrayOf(1, 2, 3))

        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        assertTrue(f.tts.spoken.isEmpty())
    }

    @Test
    fun transportFailure_setsErrorPhase_andStopsAudio() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onFailure("soket koptu")

        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertTrue(f.session.state.value.errorMessage!!.contains("soket koptu"))
        assertEquals(1, f.fakeMic.stopCalls)
        assertEquals(1, f.stt.destroyCalls)
        assertEquals(1, f.tts.destroyCalls)
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

    // -- STT -> user_text / speech_start / barge-in (protocol v2) --------------------------

    @Test
    fun sttPartialResult_showsAsDimmedPartial_inState() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onPartialResult("merh")
        assertEquals("merh", f.session.state.value.partialText)
        // The committed transcript is untouched until the final result.
        assertTrue(f.session.state.value.transcript.isEmpty())

        f.stt.listener!!.onPartialResult("merhaba jar")
        assertEquals("merhaba jar", f.session.state.value.partialText)
    }

    @Test
    fun sttFinalResult_sendsUserTextFrame_appendsLine_clearsPartial_restartsListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.stt.listener!!.onPartialResult("merhaba")

        f.stt.listener!!.onResult("merhaba jarvis")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
        assertEquals("merhaba jarvis", frame.getValue("text").jsonPrimitive.content)
        assertEquals(true, frame.getValue("utterance_final").jsonPrimitive.content.toBoolean())
        assertNull(f.session.state.value.partialText)
        assertEquals(
            listOf(TranscriptLine("user", "merhaba jarvis")),
            f.session.state.value.transcript,
        )

        // Continuous dialog: a final result re-arms the recognizer for the next turn.
        // advanceTimeBy, not advanceUntilIdle: the restart is delayed (300ms) and the
        // test scheduler does not treat a backgroundScope delay as pending work.
        advanceTimeBy(400)
        assertEquals(2, f.stt.listenCalls)
    }

    /** Two consecutive user turns are separate utterances: a second final must start a
     *  NEW line even though the role did not change. */
    @Test
    fun sttFinalResults_ofTwoTurns_becomeSeparateLines() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onResult("saat kaç")
        f.stt.listener!!.onResult("hava nasıl")

        assertEquals(
            listOf(TranscriptLine("user", "saat kaç"), TranscriptLine("user", "hava nasıl")),
            f.session.state.value.transcript,
        )
    }

    @Test
    fun sttBlankFinalResult_sendsNothing_butStillRestartsListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onResult("   ")

        assertEquals(1, f.transport.sentTexts.size) // only the hello
        assertTrue(f.session.state.value.transcript.isEmpty())
        advanceTimeBy(400) // restart delay is 300ms; advanceUntilIdle skips backgroundScope delays
        assertEquals(2, f.stt.listenCalls)
    }

    /**
     * Barge-in: the user cutting into the assistant's reply must (a) stop the device TTS
     * immediately, (b) flip the label back from SPEAKING to LISTENING, and (c) tell the
     * server exactly once per utterance via a speech_start frame.
     */
    @Test
    fun sttBeginningOfSpeech_whileJarvisSpeaks_isEcho_soNothingIsCutOff() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"uzun bir cevap"}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        val textsBefore = f.transport.sentTexts.size
        f.stt.listener!!.onBeginningOfSpeech()

        // CONTRACT CHANGE (2026-08-03, echo guard): while Jarvis's own voice is coming
        // out of the loudspeaker, a recognizer onset is that voice -- not Kadir. The
        // old contract (stop TTS + send speech_start) is exactly what made Jarvis cut
        // ITSELF off mid-sentence and trim the server's speaker-ID buffer to the echo.
        // Voice barge-in during Jarvis's speech is deliberately gone; interrupt() is
        // its deterministic replacement (see the interrupt_* tests).
        assertEquals(0, f.tts.stopCalls)
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
        assertEquals(textsBefore, f.transport.sentTexts.size)
    }

    /** A TTS done arriving after an echo onset (the utterance reporting back) must not
     *  corrupt the phase or the multi-part counter. */
    @Test
    fun ttsDoneArrivingAfterAnEchoOnset_doesNotCorruptPhase() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"uzun bir cevap"}""")

        f.stt.listener!!.onBeginningOfSpeech()
        f.tts.listener!!.onUtteranceDone() // the interrupted utterance reports done

        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        // And the NEXT reply still tracks its own utterance correctly.
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"yeni cevap"}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
        f.tts.listener!!.onUtteranceDone()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    /**
     * The anti-stall guard (the v2 counterpart of "SPEAKING settles to LISTENING"):
     * silence (SPEECH_TIMEOUT) or an unrecognized utterance (NO_MATCH) must not park the
     * call -- the recognizer re-arms and the phase stays LISTENING with no error.
     */
    @Test
    fun sttRecoverableError_restartsListening_withoutEndingTheCall() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onRecoverableError()
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        assertNull(f.session.state.value.errorMessage)

        advanceTimeBy(400) // restart delay is 300ms; advanceUntilIdle skips backgroundScope delays
        assertEquals(2, f.stt.listenCalls)

        // And again: repeated silence keeps re-arming, never escalates.
        f.stt.listener!!.onRecoverableError()
        advanceTimeBy(400)
        assertEquals(3, f.stt.listenCalls)
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    @Test
    fun sttFatalError_endsTheCall_withTurkishError() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onFatalError()

        assertEquals(VoicePhase.ERROR, f.session.state.value.phase)
        assertTrue(f.session.state.value.errorMessage!!.contains("Ses tanıma"))
        assertEquals(1, f.fakeMic.stopCalls)
        assertEquals(1, f.tts.destroyCalls)
        assertEquals(1, f.transport.closeCalls)
    }

    // -- mic capture loop -----------------------------------------------------------------

    // -- echo gate (Jarvis must never answer its own TTS) -----------------------------------

    /**
     * SAHA 4 Ağu 02:28 (hoparlör yönlendirmesinden sonra): Jarvis yine kendine cevap
     * verdi. Delik: salt-zamanlama düşürmesi onset MANDALINA bağlıydı ve Android
     * tanıyıcısı `onBeginningOfSpeech`i her zaman ATMAZ — mandal kurulmayınca
     * konuşma SIRASINDA gelen final yalnız metin kapısına (isJarvisEcho) düşüyordu,
     * tanıyıcının bozduğu kısa yankı oradan geçiyordu. Korumanın kendi kuralı
     * ("hoparlör çalarken duyulan Kadir değildir") mandaldan bağımsızdır.
     *
     * ÖLDÜREN MUTASYON: onResult'taki `echoGuardActive()` dalını mandala geri
     * bağlamak — bu test onset GÖNDERMEDEN finali verir ve düşmesini bekler.
     */
    @Test
    fun sttFinalResult_whileTheGuardIsUp_isDropped_evenWithoutAnOnset() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Saat şu an gece iki buçuk, yatsan iyi olur."}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        // onset YOK (tanıyıcı kaçırdı) ve metin, yankının tanınmayacak kadar
        // bozulmuş hâli — metin kapısı bunu yakalayamaz, yakalayan zamanlamadır.
        f.stt.listener!!.onResult("saat on gece buçuk yatsan")

        assertEquals(1, f.transport.sentTexts.size)
        assertTrue(f.session.state.value.transcript.none { it.role == "user" })
        // Düşen yankı çağrıyı asla parke etmez: tanıyıcı yeniden kurulur.
        advanceTimeBy(400)
        assertEquals(2, f.stt.listenCalls)
    }

    /**
     * Son cümle yankısının deliği: onset kaçtı, yankının finali Jarvis sustuktan
     * sonra (kuyrukta) geldi. Kuyruktaki mandalsız finali düşüremeyiz — Jarvis
     * sustuktan hemen sonra verilen gerçek kısa cevap da orada gelir (bir alttaki
     * iki test onu pinler). Onun yerine PARTIAL mandalı kurar: partial'lar,
     * onset'in aksine güvenilir ateşlenir ve konuşma SIRASINDA gelen partial bu
     * söyleyişin hoparlörden başladığının kanıtıdır.
     *
     * ÖLDÜREN MUTASYON: onPartialResult'taki mandal yükseltmesini silmek.
     */
    @Test
    fun aPartialDuringJarvisSpeech_latchesTheUtterance_soItsTailFinalDrops() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Saat şu an gece iki buçuk, yatsan iyi olur."}""")

        // onset YOK; konuşma sırasında bir partial düştü (yankının başlangıcı).
        f.stt.listener!!.onPartialResult("saat şu an")
        // Jarvis sustu; yankının bozuk finali KUYRUK içinde geliyor.
        f.tts.listener!!.onUtteranceDone()
        f.clock.now += 500
        f.stt.listener!!.onResult("saat on gece buçuk yatsan")

        assertEquals(1, f.transport.sentTexts.size)
        assertTrue(f.session.state.value.transcript.none { it.role == "user" })
    }

    @Test
    fun sttFinalResult_thatIsJarvisOwnTts_isDropped_notSent_notInTranscript() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Saat şu an akşam dokuz buçuk, başka bir şey ister misin?"}""")

        // The recognizer hears the speaker and "transcribes" Jarvis's own words.
        f.stt.listener!!.onResult("Saat şu an akşam dokuz buçuk, başka bir şey ister misin?")

        // Only the hello went out; no user_text for the echo.
        assertEquals(1, f.transport.sentTexts.size)
        // The echo adds NO user line; only Jarvis's own reply is in the transcript.
        assertEquals(
            listOf(TranscriptLine("jarvis", "Saat şu an akşam dokuz buçuk, başka bir şey ister misin?")),
            f.session.state.value.transcript,
        )
        // Listening still re-arms (the call must not stall on a dropped echo).
        advanceTimeBy(400)
        assertEquals(2, f.stt.listenCalls)
    }

    @Test
    fun sttFinalResult_partialRepeatOfJarvisTts_isAlsoDropped() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Yarın sabah dokuzda toplantın var, hazırlıklı ol."}""")

        // AEC trims imperfectly: the tail of Jarvis's own sentence comes back.
        f.stt.listener!!.onResult("sabah dokuzda toplantın var hazırlıklı ol")

        assertEquals(1, f.transport.sentTexts.size)
        assertEquals(
            listOf(TranscriptLine("jarvis", "Yarın sabah dokuzda toplantın var, hazırlıklı ol.")),
            f.session.state.value.transcript,
        )
    }

    @Test
    fun sttFinalResult_genuineUserSpeech_afterJarvisSpoke_isStillSent() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Saat şu an akşam dokuz buçuk."}""")

        // "Jarvis konuştuktan SONRA": söyleyiş bitti ve kuyruk penceresi geçti.
        // (4 Ağu koruma düzeltmesinden beri konuşma SIRASINDA gelen final mandalsız
        // da düşer — bu testin pinlediği şey o değil, metin kapısının gerçek
        // konuşmayı yutmamasıdır.)
        f.tts.listener!!.onUtteranceDone()
        f.clock.now += 2_100

        f.stt.listener!!.onResult("yarın hava nasıl olacak")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
        assertEquals("yarın hava nasıl olacak", frame.getValue("text").jsonPrimitive.content)
    }

    @Test
    fun sttFinalResult_shortRealAnswer_matchingJarvisWord_isNeverDropped() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Evet dedin mi?"}""")
        // Gerçekçi zaman çizgisi: cevap, söyleyiş bitip kuyruk geçtikten sonra gelir
        // (yukarıdaki testle aynı gerekçe).
        f.tts.listener!!.onUtteranceDone()
        f.clock.now += 2_100

        // Short answers must never be judged echo, even if Jarvis said the same word.
        f.stt.listener!!.onResult("evet")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
    }

    // -- mic capture loop -----------------------------------------------------------------

    @Test
    fun micFrames_areForwardedAsBinary_toTheTransport() = runTest {
        // UnconfinedTestDispatcher, not backgroundScope: the fake's readFrame() never
        // truly suspends, so an Unconfined launch runs the whole read/send loop eagerly
        // and synchronously inside onOpen() -- no scheduler-advance ambiguity.
        val loopScope = CoroutineScope(UnconfinedTestDispatcher())
        val f = Fixture(loopScope)
        f.fakeMic.frames += byteArrayOf(1, 2, 3)
        f.fakeMic.frames += byteArrayOf(4, 5, 6)

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
        assertEquals(1, f.fakeMic.stopCalls)
        assertEquals(1, f.stt.destroyCalls)
        assertEquals(1, f.tts.destroyCalls)
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertNull(f.session.state.value.errorMessage)
    }

    @Test
    fun stop_whileListening_stopsAudio_andClosesTransport() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.session.stop()

        assertEquals(1, f.fakeMic.stopCalls)
        assertEquals(1, f.stt.destroyCalls)
        assertEquals(1, f.tts.destroyCalls)
        assertEquals(1, f.transport.closeCalls)
        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
    }

    /**
     * Mirrors app.js's `voiceGen` guard: a callback from a generation that belonged to a
     * PREVIOUS start()/stop() cycle must not clobber the state of whatever is happening
     * now. This covers stale STT/TTS callbacks too -- a recognizer reporting its final
     * result after hang-up must not resurrect transcript lines or send user_text.
     */
    @Test
    fun lateCallbackFromAStoppedGeneration_isIgnored() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        val staleTransportListener = f.transport.listener!!
        val staleSttListener = f.stt.listener!!
        val staleTtsListener = f.tts.listener!!

        f.session.stop()
        staleTransportListener.onText("""{"type":"transcript","role":"model","text":"gec gelen"}""")
        staleSttListener.onResult("gec gelen")
        staleSttListener.onBeginningOfSpeech()
        staleTtsListener.onUtteranceDone()

        assertEquals(VoicePhase.IDLE, f.session.state.value.phase)
        assertTrue(f.session.state.value.transcript.isEmpty())
        assertEquals(1, f.transport.sentTexts.size) // hello only -- no user_text/speech_start
        assertEquals(0, f.tts.stopCalls)
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

    // -- echo GUARD (half-duplex while Jarvis speaks) ---------------------------------------
    // Production evidence (2026-08-03): 27 of the last 50 verified utterances scored in
    // the different-speaker band (~0.16) -- Jarvis's own TTS reaching the speaker-ID
    // machine. These tests pin the four ways that happened.

    /**
     * The capture loop must actually RUN inside these tests, so they use an
     * UnconfinedTestDispatcher exactly like [micFrames_areForwardedAsBinary_toTheTransport]:
     * a `channel.receive()` resumed by `emit()` then continues eagerly, with no
     * scheduler-advance ambiguity about whether the drop or the send has happened yet.
     */
    private fun speakingFixture(): Fixture {
        val f = Fixture(CoroutineScope(UnconfinedTestDispatcher()), mic = ChannelMicSource())
        f.session.start()
        f.transport.listener!!.onOpen()
        return f
    }

    private val Fixture.channelMic: ChannelMicSource get() = mic as ChannelMicSource

    /**
     * The PRODUCTION frame order. `_serve_turn` (brain/app/voice.py) sends jarvis_text,
     * transcript and turn_complete back to back, microseconds apart — the device only
     * STARTS speaking once it has them, and then speaks for seconds.
     *
     * Every echo-guard test below drives this, not a bare jarvis_text. The first
     * version of these tests sent only jarvis_text, so they asserted against an event
     * sequence production never produces and passed while the guard was, in reality,
     * off for almost the whole of Jarvis's speech.
     */
    private fun Fixture.serveTurn(reply: String) {
        transport.listener!!.onText("""{"type":"jarvis_text","text":"$reply"}""")
        transport.listener!!.onText("""{"type":"transcript","role":"jarvis","text":"$reply"}""")
        transport.listener!!.onText("""{"type":"turn_complete"}""")
    }

    @Test
    fun turnComplete_doesNotEndTheEchoGuard_theDeviceIsStillSpeaking() = runTest {
        // The regression this whole section exists for: turn_complete means "no more
        // jarvis_text is coming", NOT "the loudspeaker went quiet". Only the TTS engine
        // can say the latter, and it says it with onUtteranceDone.
        val f = speakingFixture()
        f.serveTurn("Bugün hava güneşli, dışarı çıkabilirsin.")

        f.clock.now += 1_000            // TTS is a second into a multi-second sentence
        f.channelMic.emit(byteArrayOf(1))

        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
        assertEquals(1, f.channelMic.readCount)
        assertEquals(0, f.transport.sentBinaries.size)
    }

    @Test
    fun turnComplete_midSpeech_doesNotLetTheEchoCutJarvisOff() = runTest {
        val f = speakingFixture()
        f.serveTurn("Bugün hava güneşli, dışarı çıkabilirsin.")
        val textsBefore = f.transport.sentTexts.size

        f.clock.now += 1_000
        f.stt.listener!!.onBeginningOfSpeech()

        assertEquals(0, f.tts.stopCalls)
        assertEquals(textsBefore, f.transport.sentTexts.size)
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
    }

    @Test
    fun turnComplete_withNoReplyToSpeak_settlesToListeningAtOnce() = runTest {
        // The empty-reply path: turn_complete with nothing spoken must NOT leave the
        // call stuck in SPEAKING waiting for an onUtteranceDone that will never come.
        val f = speakingFixture()
        f.transport.listener!!.onText("""{"type":"turn_complete"}""")

        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        f.clock.now += VoiceSession.ECHO_TAIL_MS + 1
        f.channelMic.emit(byteArrayOf(4))
        assertEquals(1, f.transport.sentBinaries.size)
    }

    @Test
    fun interrupt_isReachableWhileJarvisActuallySpeaks() = runTest {
        // The tap replaced voice barge-in, so it must be available for the WHOLE of
        // Jarvis's speech -- not just the microseconds before turn_complete arrives.
        val f = speakingFixture()
        f.serveTurn("Uzun bir cevap veriyorum, birkaç saniye sürüyor.")

        f.clock.now += 2_000
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.session.interrupt()
        assertEquals(1, f.tts.stopCalls)
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    @Test
    fun micFrames_areNotSentWhileJarvisIsSpeaking() = runTest {
        val f = speakingFixture()
        f.serveTurn("Merhaba Kadir.")

        f.channelMic.emit(byteArrayOf(1, 2, 3))

        // Read off the mic but NOT forwarded: the server's speaker-ID buffer must never
        // contain Jarvis's own voice. Both halves asserted -- a silent loop would make
        // the second assertion meaningless on its own.
        assertEquals(1, f.channelMic.readCount)
        assertEquals(0, f.transport.sentBinaries.size)
    }

    @Test
    fun micFrames_resumeOnceTheEchoTailHasExpired() = runTest {
        val f = speakingFixture()
        f.serveTurn("Merhaba Kadir.")
        f.channelMic.emit(byteArrayOf(1))
        assertEquals(0, f.transport.sentBinaries.size)

        // Jarvis finishes; the tail still swallows audio...
        f.tts.listener!!.onUtteranceDone()
        f.channelMic.emit(byteArrayOf(2))
        assertEquals(0, f.transport.sentBinaries.size)

        // ...until it expires, and then Kadir's audio flows again.
        f.clock.now += VoiceSession.ECHO_TAIL_MS + 1
        f.channelMic.emit(byteArrayOf(3))
        assertEquals(3, f.channelMic.readCount)
        assertEquals(1, f.transport.sentBinaries.size)
        assertTrue(byteArrayOf(3).contentEquals(f.transport.sentBinaries[0]))
    }

    @Test
    fun micFrames_resumeAfterTheHardCeiling_evenIfTtsNeverReportsDone() = runTest {
        // A TTS engine that never fires onUtteranceDone would otherwise leave ttsActive
        // pinned above zero and the call permanently DEAF -- far worse than the bug
        // being fixed. The ceiling is what makes that impossible.
        val f = speakingFixture()
        f.serveTurn("Uzun bir cevap.")

        f.clock.now += VoiceSession.MAX_SPEAK_GUARD_MS + 1
        f.channelMic.emit(byteArrayOf(9))

        assertEquals(1, f.transport.sentBinaries.size)
    }

    @Test
    fun onsetDuringJarvisSpeech_doesNotCutJarvisOff_andSendsNoSpeechStart() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Bugün hava güneşli."}""")
        val textsBefore = f.transport.sentTexts.size

        // The recognizer hears the loudspeaker and reports speech onset.
        f.stt.listener!!.onBeginningOfSpeech()

        // Jarvis must NOT interrupt itself, and no speech_start may reach the server
        // (it would trim the speaker-ID buffer to the echo's onset).
        assertEquals(0, f.tts.stopCalls)
        assertEquals(textsBefore, f.transport.sentTexts.size)
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)
    }

    @Test
    fun shortEcho_thatTheTextGateCannotJudge_isStillDropped() = runTest {
        // ECHO_MIN_CHARS deliberately lets short finals through, so the text gate can
        // never catch this one. Timing can.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Tabii."}""")
        val textsBefore = f.transport.sentTexts.size

        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onResult("tabii")

        assertEquals(textsBefore, f.transport.sentTexts.size)
        assertTrue(f.session.state.value.transcript.none { it.role == "user" })
    }

    @Test
    fun mangledEcho_thatNoLongerMatchesTheText_isStillDropped() = runTest {
        // The recognizer garbles what it hears off the speaker, so normalized
        // containment fails and isJarvisEcho() returns false. The onset latch does not
        // care what the words turned into.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText(
            """{"type":"jarvis_text","text":"Yarın sabah dokuzda toplantın var, hazırlıklı ol."}"""
        )
        val textsBefore = f.transport.sentTexts.size

        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onResult("yalın saba do kozda top lantı marş")

        assertEquals(textsBefore, f.transport.sentTexts.size)
        assertTrue(f.session.state.value.transcript.none { it.role == "user" })
    }

    @Test
    fun theOnsetLatch_doesNotLeakIntoTheNextUtterance() = runTest {
        // The failure class this file's history is full of: a per-turn flag set in one
        // branch and cleared in another. The latch's life is exactly one listen cycle.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Tabii."}""")
        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onResult("tabii")          // dropped, latch consumed
        advanceTimeBy(400)

        // Jarvis has finished SPEAKING (only onUtteranceDone says that -- turn_complete
        // does not) and the tail has passed; Kadir speaks for real.
        f.transport.listener!!.onText("""{"type":"turn_complete"}""")
        f.tts.listener!!.onUtteranceDone()
        f.clock.now += VoiceSession.ECHO_TAIL_MS + 1
        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onResult("yarın hava nasıl olacak")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
        assertEquals("yarın hava nasıl olacak", frame.getValue("text").jsonPrimitive.content)
    }

    @Test
    fun aMergedUtterance_thatStartedAsEchoButEndsAsKadir_isNotSwallowed() = runTest {
        // The fifth instance of this file's recurring bug class, found in review. One
        // recognition turn can hold BOTH: the echo trips the onset, Jarvis stops, and
        // Kadir answers inside the recognizer's 1.5 s silence window so no endpoint
        // occurs. The single final then carries KADIR's words -- and the latch alone
        // dropped them silently: his sentence appears on screen, vanishes, no reply.
        val f = speakingFixture()
        f.serveTurn("Bugün hava güneşli.")
        f.stt.listener!!.onBeginningOfSpeech()           // the echo trips the onset

        f.tts.listener!!.onUtteranceDone()               // Jarvis actually stops
        f.clock.now += VoiceSession.ECHO_TAIL_MS + 1     // ...and the tail runs out
        f.stt.listener!!.onResult("peki yarın ne olacak")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
        assertEquals("peki yarın ne olacak", frame.getValue("text").jsonPrimitive.content)
    }

    @Test
    fun aMangledEchoFinalizingInsideTheTail_isStillDropped() = runTest {
        // The other side of the same rule: a real echo's final lands ~1.5 s after the
        // sound stopped (COMPLETE_SILENCE_MS), i.e. INSIDE the tail -- which is exactly
        // what ECHO_TAIL_MS is sized for. Mangled past textual recognition, so only the
        // timing can catch it.
        val f = speakingFixture()
        f.serveTurn("Yarın sabah dokuzda toplantın var.")
        f.stt.listener!!.onBeginningOfSpeech()
        val textsBefore = f.transport.sentTexts.size

        f.tts.listener!!.onUtteranceDone()
        f.clock.now += 1_500                              // the recognizer's endpoint
        f.stt.listener!!.onResult("yalın saba do kozda top lantı marş")

        assertEquals(textsBefore, f.transport.sentTexts.size)
        assertTrue(f.session.state.value.transcript.none { it.role == "user" })
    }

    @Test
    fun realBargeIn_whenJarvisIsSilent_stillStopsTtsAndSendsSpeechStart() = runTest {
        // The guard must not disable ordinary onset handling on a listening call.
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onBeginningOfSpeech()

        assertEquals(1, f.tts.stopCalls)
        assertEquals("speech_start", lastSentTextJson(f.transport).getValue("type").jsonPrimitive.content)
    }

    @Test
    fun interrupt_cutsJarvisOff_andReopensTheMicImmediately() = runTest {
        val f = speakingFixture()
        f.serveTurn("Uzun bir cevap veriyorum.")

        f.session.interrupt()

        assertEquals(1, f.tts.stopCalls)
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        // No tail is served: Kadir asked for the floor, so his audio flows at once.
        f.channelMic.emit(byteArrayOf(7))
        assertEquals(1, f.transport.sentBinaries.size)
    }

    @Test
    fun interrupt_thenSpeaking_isNotReadAsTheEchoOfTheCutOffSentence() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"Uzun bir cevap veriyorum."}""")

        f.session.interrupt()
        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onResult("dur bir saniye")

        val frame = lastSentTextJson(f.transport)
        assertEquals("user_text", frame.getValue("type").jsonPrimitive.content)
        assertEquals("dur bir saniye", frame.getValue("text").jsonPrimitive.content)
    }

    @Test
    fun interrupt_onAListeningCall_isANoOp() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.session.interrupt()

        assertEquals(0, f.tts.stopCalls)
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
    }

    // -- timeline instrumentation (Task 11: the first-words report needs data, not
    // hypotheses) -------------------------------------------------------------------------
    // The client logged nothing about the session timeline, so a "my first words were
    // lost" report could not be localized to mic-open vs recognizer-arm vs echo-guard.
    // These tests pin the greppable event stream's ordering, its volume discipline
    // (first-partial-only, transition-only mic gate), and that the three echo-guard drop
    // reasons are distinguishable from a clean send -- instrumentation only, no assertion
    // here may depend on a phase/frame/frame-count outcome changing.

    private fun eventName(line: String): String = line.substringAfter("ev=").substringBefore(' ')

    @Test
    fun timeline_ordersStartOpenArmBeginFirstPartialFinal_correctly() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.stt.listener!!.onBeginningOfSpeech()
        f.stt.listener!!.onPartialResult("merhaba")
        f.stt.listener!!.onPartialResult("merhaba jarvis")
        f.stt.listener!!.onResult("merhaba jarvis")

        val names = f.lines.map(::eventName)
        val start = names.indexOf("start")
        val open = names.indexOf("open")
        val arm = names.indexOf("stt.arm")
        val begin = names.indexOf("stt.begin")
        val firstPartial = names.indexOf("stt.first_partial")
        val final = names.indexOf("stt.final")

        assertTrue("start=$start open=$open arm=$arm begin=$begin firstPartial=$firstPartial final=$final in ${f.lines}", start >= 0)
        assertTrue(open > start)
        assertTrue(arm > open)
        assertTrue(begin > arm)
        assertTrue(firstPartial > begin)
        assertTrue(final > firstPartial)
    }

    @Test
    fun timeline_logsOnlyTheFirstPartial_perListenCycle() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onPartialResult("m")
        f.stt.listener!!.onPartialResult("me")
        f.stt.listener!!.onPartialResult("mer")

        val firstPartialLines = f.lines.filter { eventName(it) == "stt.first_partial" }
        // ONE line for three partials -- the datum is time-to-first-partial, not every
        // interim hypothesis (those tick many times a second).
        assertEquals(1, firstPartialLines.size)
        // And it must carry the FIRST call's data ("m", length 1), not the last.
        assertTrue(firstPartialLines[0].contains("len=1 "))
    }

    @Test
    fun timeline_final_logsOutSent_onACleanFinal() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()

        f.stt.listener!!.onResult("merhaba jarvis")

        val finalLine = f.lines.first { eventName(it) == "stt.final" }
        assertTrue(finalLine, finalLine.contains("out=sent"))
    }

    /** Reuses the fake-clock echo-guard setup from
     *  [sttFinalResult_whileTheGuardIsUp_isDropped_evenWithoutAnOnset]: a final arriving
     *  while Jarvis's TTS is actively playing must log the SPEAKING drop reason, not just
     *  drop silently. */
    @Test
    fun timeline_final_logsOutDropSpeaking_whileJarvisIsTalking() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText(
            """{"type":"jarvis_text","text":"Saat şu an gece iki buçuk, yatsan iyi olur."}"""
        )
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.stt.listener!!.onResult("saat on gece buçuk yatsan")

        val finalLine = f.lines.first { eventName(it) == "stt.final" }
        assertTrue(finalLine, finalLine.contains("out=drop_speaking"))
    }

    @Test
    fun timeline_micGate_logsOnlyOnTransitions_notPerFrame() = runTest {
        val f = speakingFixture()
        f.lines.clear() // only care about the gate lines from here on

        f.serveTurn("Merhaba Kadir, bugün hava çok güzel.")
        // Several frames while Jarvis is actively talking: the gate closes ONCE and
        // stays closed -- must not log per frame (mic frames tick 50-100/s).
        f.channelMic.emit(byteArrayOf(1))
        f.channelMic.emit(byteArrayOf(2))
        f.channelMic.emit(byteArrayOf(3))

        val afterSpeaking = f.lines.filter { eventName(it) == "mic.gate" }
        assertEquals(1, afterSpeaking.size)
        assertTrue(afterSpeaking[0], afterSpeaking[0].contains("send=false"))
        assertTrue(afterSpeaking[0], afterSpeaking[0].contains("why=speaking"))

        // Jarvis stops and the tail expires: the gate reopens exactly once, however many
        // frames arrive afterwards.
        f.tts.listener!!.onUtteranceDone()
        f.clock.now += VoiceSession.ECHO_TAIL_MS + 1
        f.channelMic.emit(byteArrayOf(4))
        f.channelMic.emit(byteArrayOf(5))

        val afterReopen = f.lines.filter { eventName(it) == "mic.gate" }
        assertEquals(2, afterReopen.size) // the original close + one reopen, nothing more
        assertTrue(afterReopen[1], afterReopen[1].contains("send=true"))
        assertTrue(afterReopen[1], afterReopen[1].contains("why=clear"))
    }

    /** The gate can close for the TAIL reason without ever having been observed closed
     *  for "speaking" first -- e.g. a short reply where no mic frame happened to arrive
     *  during the brief speaking window. `why` must still say `tail`, not `speaking`. */
    @Test
    fun timeline_micGate_reportsTailAsTheReason_whenTheGateFirstClosesDuringTheTail() = runTest {
        val f = speakingFixture()
        f.serveTurn("Kısa cevap.")
        f.lines.clear()

        f.tts.listener!!.onUtteranceDone() // Jarvis stops; no frame arrived while speaking
        f.channelMic.emit(byteArrayOf(1)) // first frame the loop sees is inside the tail

        val gateLines = f.lines.filter { eventName(it) == "mic.gate" }
        assertEquals(1, gateLines.size)
        assertTrue(gateLines[0], gateLines[0].contains("send=false"))
        assertTrue(gateLines[0], gateLines[0].contains("why=tail"))
    }

    /**
     * Fix round 1 (task review, Important #2): [VoiceSession] is a long-lived instance --
     * [VoiceCallViewModel] constructs it once and calls start()/stop() repeatedly across
     * many calls. A call that ends MID-UTTERANCE (stop() here, but any of endSession's six
     * paths applies equally) must not leave `firstPartialLogged` stuck true: the next
     * call's real first partial is the exact datum this whole slice exists to capture, and
     * silently skipping it would defeat the instrumentation for every call after the first.
     */
    @Test
    fun timeline_firstPartial_resetsAcrossGenerations_soTheNextCallsFirstPartialIsLoggedToo() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.stt.listener!!.onPartialResult("ilk") // this generation's first partial -- logged

        f.session.stop() // ends mid-utterance, before any final/relisten() ever ran

        f.session.start() // a LATER generation (endSession's own compareAndSet also bumps
        // the counter once on teardown, so this is not simply "+1" -- the point pinned
        // here is only that it differs from the first, not the exact number).
        f.transport.listener!!.onOpen()
        f.stt.listener!!.onPartialResult("ikinci") // must ALSO log, not be silently skipped

        val firstPartialLines = f.lines.filter { eventName(it) == "stt.first_partial" }
        assertEquals(2, firstPartialLines.size)
        val firstGen = firstPartialLines[0].substringAfter("gen=").substringBefore(' ').toInt()
        val secondGen = firstPartialLines[1].substringAfter("gen=").substringBefore(' ').toInt()
        assertTrue(
            "expected the second call's generation to be later than the first's " +
                "(first=$firstGen second=$secondGen)",
            secondGen > firstGen,
        )
    }
}
