package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ
import com.jarvis.data.voice.protocol.TranscriptLine
import com.jarvis.data.voice.protocol.VoiceServerEvent
import com.jarvis.data.voice.protocol.buildSpeechStartFrame
import com.jarvis.data.voice.protocol.buildUserTextFrame
import com.jarvis.data.voice.protocol.buildVoiceHello
import com.jarvis.data.voice.protocol.parseVoiceServerEvent
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * The live-voice state machine (Katman 3b), protocol v2. Owns no Android type directly --
 * the socket is [VoiceTransport], the microphone is [MicSource], speech recognition is
 * [SpeechToText], speech output is [SpeechSynthesis] -- so every transition here runs and
 * is asserted on the plain JVM (VoiceSessionTest). Only the four real implementations
 * ([OkHttpVoiceTransport], [AndroidMicSource], [AndroidSpeechToText],
 * [AndroidTextToSpeech]) touch Android/OkHttp APIs.
 *
 * Frame flow, matching brain/app/voice_protocol.py's protocol v2 exactly:
 *  - client -> server: hello TEXT frame on open (with `client_caps`), then `speech_start`
 *    / `user_text` TEXT frames from the on-device recognizer, plus raw PCM16 mono 16kHz
 *    BINARY frames from [mic] -- the mic stream now feeds server-side speaker-ID only.
 *  - server -> client: TEXT frames only, one JSON event each, parsed by
 *    [parseVoiceServerEvent]. `jarvis_text` goes to the on-device [tts]; there is NO
 *    binary model audio anymore and any binary frame from the server is ignored.
 *
 * Barge-in: the recognizer's onBeginningOfSpeech both stops [tts] and sends one
 * `speech_start` frame. Continuous dialog: after every final result and every
 * recoverable recognizer error the session re-arms [SpeechToText.listen] -- the
 * anti-stall guard replacing the old SPEAKING-settles-to-LISTENING quiet timer.
 *
 * [generation] mirrors brain/web/app.js's `voiceGen`: every start() bumps it, and every
 * transport/STT/TTS callback closure captures the value at connect time -- a callback
 * from a PREVIOUS (already torn down) generation can then tell it's stale and no-op
 * instead of resurrecting dead state. [endSession] uses `compareAndSet` as the single
 * "claim teardown" point, so onClosed/onFailure/stop()/an `error` event racing each
 * other can only ever tear down once. Teardown ORDER -- audio first, transport last --
 * mirrors app.js's `cleanupVoice()`.
 */
class VoiceSession(
    private val transport: VoiceTransport,
    private val mic: MicSource,
    private val stt: SpeechToText,
    private val tts: SpeechSynthesis,
    private val tokenProvider: () -> String?,
    private val deviceHint: String,
    private val scope: CoroutineScope,
    private val voiceUrl: String,
) {
    private val _state = MutableStateFlow(VoiceUiState())
    val state: StateFlow<VoiceUiState> = _state.asStateFlow()

    // AtomicInteger, not a plain var: transport callbacks arrive on the transport's own
    // thread (OkHttp's reader thread in production, see VoiceTransportListener's doc)
    // while start()/stop() are called from the UI thread -- increments and the
    // teardown-claiming compareAndSet below must be atomic across both.
    private val generation = AtomicInteger(0)

    // The generation guard alone leaves a TOCTOU window the JS reference never had (it
    // is single-threaded): a callback can pass its gen check, LOSE the CPU while
    // endSession completes a full teardown, then resume -- starting the mic after
    // teardown and resurrecting dead state (review Critical #1, pinned by
    // stop_racingOnOpen_neverLeavesTheMicRunning_orResurrectsState). Every callback
    // body and endSession run under this monitor, so check-then-act is atomic. Bodies
    // hold it only for ms-scale device calls; the mic READ loop stays outside.
    private val lock = Any()

    // Written only from inside a transport callback (onOpen) and read/cancelled from
    // endSession, which can itself run on either that thread or the UI thread (stop()).
    // @Volatile guarantees a write on one thread is visible when the other reads it.
    @Volatile
    private var micJob: Job? = null

    // True right after a turn_complete or a user final: the NEXT transcript fragment
    // starts a new line even for the same role — two consecutive user turns are separate
    // utterances. Only touched under [lock], no extra synchronization needed.
    private var turnBoundary = false

    // Utterances handed to the TTS but not yet reported done. A reply can arrive as
    // several jarvis_text events; SPEAKING only settles back to LISTENING when this
    // reaches zero, so the first done of a multi-part reply doesn't flicker the label.
    // Reset on barge-in and turn_complete (guarded against going negative).
    // Only touched under [lock].
    private var ttsActive = 0

    // Delayed recognizer re-arm after a final result / recoverable error. The small
    // delay keeps a pathological device (instant NO_MATCH loops) from hot-spinning the
    // recognition service. Only touched under [lock].
    private var sttRestartJob: Job? = null

    /** Starts a call. No-op if one is already connecting/live. */
    fun start() {
        val phase = _state.value.phase
        if (phase == VoicePhase.CONNECTING || phase == VoicePhase.LISTENING || phase == VoicePhase.SPEAKING) {
            return
        }
        val token = tokenProvider()
        if (token == null) {
            _state.update {
                VoiceUiState(phase = VoicePhase.ERROR, errorMessage = "Oturum bulunamadı, tekrar giriş yap.")
            }
            return
        }

        val gen = generation.incrementAndGet()
        _state.update { VoiceUiState(phase = VoicePhase.CONNECTING) }

        transport.connect(
            voiceUrl,
            object : VoiceTransportListener {
                override fun onOpen(): Unit = synchronized(lock) {
                    if (gen != generation.get()) return
                    transport.sendText(buildVoiceHello(token = token, deviceHint = deviceHint))
                    tts.start(ttsListener(gen))
                    stt.start(sttListener(gen))
                    try {
                        // Can throw on real hardware (mic held by an actual phone call)
                        // — and this runs on OkHttp's reader thread, where an escape
                        // kills the process.
                        mic.start(AUDIO_IN_RATE_HZ)
                    } catch (t: RuntimeException) {
                        endSession(gen, "Mikrofon açılamadı. Aramayı yeniden başlatmayı dene.")
                        return
                    }
                    startMicLoop(gen)
                    stt.listen()
                    _state.update { it.copy(phase = VoicePhase.LISTENING) }
                }

                override fun onText(text: String): Unit = synchronized(lock) {
                    if (gen != generation.get()) return
                    handleServerEvent(gen, parseVoiceServerEvent(text))
                }

                override fun onBinary(bytes: ByteArray) {
                    // Protocol v2: the server never sends audio down -- TTS is on-device.
                    // A binary frame here is a protocol bug server-side; ignore it.
                }

                override fun onClosed() {
                    endSession(gen, errorMessage = null)
                }

                override fun onFailure(message: String) {
                    endSession(gen, errorMessage = "Bağlantı hatası: $message")
                }
            },
        )
    }

    /** Ends a call the user (or the host: lifecycle/navigation) initiated stopping. */
    fun stop() {
        if (_state.value.phase == VoicePhase.IDLE) return
        endSession(generation.get(), errorMessage = null)
    }

    /**
     * Reports an error that happened BEFORE any socket existed -- namely a denied
     * RECORD_AUDIO permission at the Activity layer. Ignored while a call is actually
     * active so a stale report cannot clobber a live session.
     */
    fun reportError(message: String) {
        val phase = _state.value.phase
        if (phase == VoicePhase.CONNECTING || phase == VoicePhase.LISTENING || phase == VoicePhase.SPEAKING) {
            return
        }
        _state.update { it.copy(phase = VoicePhase.ERROR, errorMessage = message) }
    }

    private fun sttListener(gen: Int) = object : SpeechToTextListener {
        override fun onBeginningOfSpeech(): Unit = synchronized(lock) {
            if (gen != generation.get()) return
            // Barge-in: the user cutting in kills the assistant's voice immediately --
            // and one speech_start frame per utterance (the recognizer fires this at
            // most once per listen() turn, so no extra dedup is needed here).
            tts.stop()
            ttsActive = 0
            transport.sendText(buildSpeechStartFrame())
            _state.update { it.copy(phase = VoicePhase.LISTENING) }
        }

        override fun onPartialResult(text: String): Unit = synchronized(lock) {
            if (gen != generation.get()) return
            _state.update { it.copy(partialText = text) }
        }

        override fun onResult(text: String): Unit = synchronized(lock) {
            if (gen != generation.get()) return
            if (text.isNotBlank()) {
                _state.update {
                    it.copy(partialText = null, transcript = appendLine(it, "user", text))
                }
                turnBoundary = true
                transport.sendText(buildUserTextFrame(text))
            }
            relisten(gen)
        }

        override fun onRecoverableError(): Unit = synchronized(lock) {
            if (gen != generation.get()) return
            _state.update { it.copy(partialText = null) }
            relisten(gen)
        }

        override fun onFatalError() {
            endSession(gen, "Ses tanıma kullanılamıyor. Aramayı yeniden başlatmayı dene.")
        }
    }

    private fun ttsListener(gen: Int) = object : SpeechSynthesisListener {
        override fun onUtteranceDone(): Unit = synchronized(lock) {
            if (gen != generation.get()) return
            if (ttsActive > 0) ttsActive--
            if (ttsActive == 0 && _state.value.phase == VoicePhase.SPEAKING) {
                _state.update { it.copy(phase = VoicePhase.LISTENING) }
            }
        }
    }

    /**
     * Re-arms the recognizer after a short pause. Delayed, not immediate: some devices
     * answer a fresh startListening with an instant NO_MATCH, and a synchronous restart
     * would hot-spin the recognition service. The generation re-check after the delay
     * keeps a teardown that happened mid-pause from resurrecting the recognizer.
     */
    private fun relisten(gen: Int) {
        sttRestartJob?.cancel()
        sttRestartJob = scope.launch {
            delay(STT_RESTART_DELAY_MS)
            synchronized(lock) {
                if (gen == generation.get()) stt.listen()
            }
        }
    }

    /**
     * Merges one fragment into the running transcript: consecutive fragments from the
     * same role join into one line (the UI renders one bubble per line), a role change
     * or a turn boundary starts a new line.
     */
    private fun appendLine(state: VoiceUiState, role: String, text: String): List<TranscriptLine> {
        val last = state.transcript.lastOrNull()
        return if (!turnBoundary && last != null && last.role == role) {
            state.transcript.dropLast(1) + TranscriptLine(last.role, last.text + " " + text)
        } else {
            state.transcript + TranscriptLine(role, text)
        }
    }

    private fun handleServerEvent(gen: Int, event: VoiceServerEvent?) {
        when (event) {
            is VoiceServerEvent.Transcript -> {
                val merged = appendLine(_state.value, event.role, event.text)
                turnBoundary = false
                _state.update { it.copy(transcript = merged) }
            }
            is VoiceServerEvent.JarvisText -> {
                ttsActive++
                tts.speak(event.text)
                val merged = appendLine(_state.value, "jarvis", event.text)
                turnBoundary = false
                _state.update { it.copy(phase = VoicePhase.SPEAKING, transcript = merged) }
            }
            is VoiceServerEvent.TurnComplete -> {
                turnBoundary = true
                ttsActive = 0
                _state.update { it.copy(phase = VoicePhase.LISTENING) }
            }
            is VoiceServerEvent.Error ->
                endSession(gen, errorMessage = "Hata: ${event.message}")
            is VoiceServerEvent.Speaker ->
                _state.update { it.copy(lastSpeakerVerified = event.verified) }
            null -> Unit // unknown type or malformed frame -- ignore, do not crash the session
        }
    }

    /**
     * The single teardown-claiming point. `compareAndSet(gen, gen + 1)` succeeds for
     * exactly ONE caller per generation: if `stop()` and a racing `onFailure` both call
     * this for the same live call, only the first actually stops the mic/STT/TTS/socket
     * and updates state -- the second sees `generation` already moved and no-ops.
     */
    private fun endSession(gen: Int, errorMessage: String?): Unit = synchronized(lock) {
        if (!generation.compareAndSet(gen, gen + 1)) return
        micJob?.cancel()
        micJob = null
        sttRestartJob?.cancel()
        sttRestartJob = null
        ttsActive = 0
        mic.stop()
        stt.destroy()
        tts.destroy()
        transport.close()
        _state.update {
            it.copy(
                phase = if (errorMessage != null) VoicePhase.ERROR else VoicePhase.IDLE,
                errorMessage = errorMessage,
                partialText = null,
            )
        }
    }

    private fun startMicLoop(gen: Int) {
        micJob = scope.launch {
            while (isActive && gen == generation.get()) {
                val frame = mic.readFrame() ?: break
                if (gen != generation.get()) break
                transport.sendBinary(frame)
            }
        }
    }

    private companion object {
        /** Pause before re-arming the recognizer after a turn or a recoverable error.
         *  Short enough to feel continuous, long enough to break instant-NO_MATCH loops. */
        const val STT_RESTART_DELAY_MS = 300L
    }
}
