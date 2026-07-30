package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.TranscriptLine
import kotlinx.coroutines.CoroutineScope
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

    private class Fixture(scope: CoroutineScope, token: String? = "tok-xyz") {
        val transport = FakeVoiceTransport()
        val mic = FakeMicSource()
        val stt = FakeSpeechToText()
        val tts = FakeSpeechSynthesis()
        val session = VoiceSession(
            transport = transport,
            mic = mic,
            stt = stt,
            tts = tts,
            tokenProvider = { token },
            deviceHint = "android-phone",
            scope = scope,
            voiceUrl = "wss://jarvis-voice.example/ws/voice",
        )
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
        assertEquals(16000, f.mic.startedRate)
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
    fun turnComplete_revertsFromSpeakingBackToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"selam"}""")
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
        assertEquals(1, f.mic.stopCalls)
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
    fun sttBeginningOfSpeech_bargeIn_stopsTts_sendsSpeechStart_returnsToListening() = runTest {
        val f = Fixture(backgroundScope)
        f.session.start()
        f.transport.listener!!.onOpen()
        f.transport.listener!!.onText("""{"type":"jarvis_text","text":"uzun bir cevap"}""")
        assertEquals(VoicePhase.SPEAKING, f.session.state.value.phase)

        f.stt.listener!!.onBeginningOfSpeech()

        assertEquals(1, f.tts.stopCalls)
        assertEquals(VoicePhase.LISTENING, f.session.state.value.phase)
        assertEquals("speech_start", lastSentTextJson(f.transport).getValue("type").jsonPrimitive.content)
    }

    /** A TTS done arriving AFTER the barge-in (the interrupted utterance reporting back)
     *  must not corrupt the phase or the multi-part counter. */
    @Test
    fun ttsDoneArrivingAfterBargeIn_doesNotCorruptPhase() = runTest {
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
        assertEquals(1, f.mic.stopCalls)
        assertEquals(1, f.tts.destroyCalls)
        assertEquals(1, f.transport.closeCalls)
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

        assertEquals(1, f.mic.stopCalls)
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
}
