package com.jarvis.data.voice.session

import com.jarvis.data.voice.protocol.AUDIO_IN_RATE_HZ
import com.jarvis.data.voice.protocol.AUDIO_OUT_RATE_HZ
import com.jarvis.data.voice.protocol.TranscriptLine
import com.jarvis.data.voice.protocol.VoiceServerEvent
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
 * The live-voice state machine (Katman 3b). Owns no Android type directly -- the socket
 * is [VoiceTransport], the microphone is [MicSource], the speaker is [SpeakerSink] -- so
 * every transition here runs and is asserted on the plain JVM (VoiceSessionTest). Only the
 * three real implementations ([OkHttpVoiceTransport], [AndroidMicSource],
 * [AndroidSpeakerSink]) touch Android/OkHttp APIs.
 *
 * Frame flow, matching brain/app/voice_protocol.py exactly:
 *  - client -> server: hello TEXT frame on open, then raw PCM16 mono 16kHz BINARY frames
 *    from [mic] as fast as they're captured.
 *  - server -> client: raw PCM16 mono 24kHz BINARY frames go straight to [speaker]; TEXT
 *    frames are one JSON event each, parsed by [parseVoiceServerEvent].
 *
 * [generation] mirrors brain/web/app.js's `voiceGen`: every start() bumps it, and every
 * transport callback closure captures the value at connect time -- a callback from a
 * PREVIOUS (already torn down) socket can then tell it's stale and no-op instead of
 * resurrecting dead state. [endSession] uses `compareAndSet` as the single "claim
 * teardown" point, so onClosed/onFailure/stop()/an `error` event racing each other can
 * only ever tear down once. Teardown ORDER -- audio first, transport last -- mirrors
 * app.js's `cleanupVoice()`.
 */
class VoiceSession(
    private val transport: VoiceTransport,
    private val mic: MicSource,
    private val speaker: SpeakerSink,
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

    // True right after a turn_complete: the NEXT transcript fragment starts a new line
    // even for the same role — two consecutive user turns are separate utterances.
    // Only touched under [lock] (onText/endSession), no extra synchronization needed.
    private var turnBoundary = false

    // A barged-into model turn can end with NO turn_complete (voice.py), which would
    // leave the label stuck on "Konuşuyor…". Every binary frame re-arms this; when
    // model audio stays quiet for [QUIET_WINDOW_MS], SPEAKING settles to LISTENING.
    // Only touched under [lock].
    private var quietJob: Job? = null

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
                    try {
                        // Both can throw on real hardware (mic held by an actual phone
                        // call, AudioTrack init failure) — and this runs on OkHttp's
                        // reader thread, where an escape kills the process.
                        mic.start(AUDIO_IN_RATE_HZ)
                        speaker.start(AUDIO_OUT_RATE_HZ)
                    } catch (t: RuntimeException) {
                        endSession(gen, "Mikrofon veya hoparlör açılamadı. Aramayı yeniden başlatmayı dene.")
                        return
                    }
                    startMicLoop(gen)
                    _state.update { it.copy(phase = VoicePhase.LISTENING) }
                }

                override fun onText(text: String): Unit = synchronized(lock) {
                    if (gen != generation.get()) return
                    handleServerEvent(gen, parseVoiceServerEvent(text))
                }

                override fun onBinary(bytes: ByteArray): Unit = synchronized(lock) {
                    if (gen != generation.get()) return
                    speaker.write(bytes)
                    _state.update { it.copy(phase = VoicePhase.SPEAKING) }
                    quietJob?.cancel()
                    quietJob = scope.launch {
                        delay(QUIET_WINDOW_MS)
                        synchronized(lock) {
                            if (gen == generation.get() && _state.value.phase == VoicePhase.SPEAKING) {
                                _state.update { it.copy(phase = VoicePhase.LISTENING) }
                            }
                        }
                    }
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

    private fun handleServerEvent(gen: Int, event: VoiceServerEvent?) {
        when (event) {
            is VoiceServerEvent.Transcript -> {
                val boundary = turnBoundary
                turnBoundary = false
                _state.update {
                    // The server streams transcription word by word; consecutive
                    // fragments from the same role merge into one line, or the UI
                    // renders one bubble per word (saha, 26 Tem 2026). A turn boundary
                    // breaks the merge even for the same role.
                    val last = it.transcript.lastOrNull()
                    val merged = if (!boundary && last != null && last.role == event.role) {
                        it.transcript.dropLast(1) +
                            TranscriptLine(last.role, last.text + " " + event.text)
                    } else {
                        it.transcript + TranscriptLine(event.role, event.text)
                    }
                    it.copy(transcript = merged)
                }
            }
            is VoiceServerEvent.TurnComplete -> {
                turnBoundary = true
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
     * this for the same live call, only the first actually stops the mic/speaker/socket
     * and updates state -- the second sees `generation` already moved and no-ops.
     */
    private fun endSession(gen: Int, errorMessage: String?): Unit = synchronized(lock) {
        if (!generation.compareAndSet(gen, gen + 1)) return
        micJob?.cancel()
        micJob = null
        quietJob?.cancel()
        quietJob = null
        mic.stop()
        speaker.stop()
        transport.close()
        _state.update {
            it.copy(
                phase = if (errorMessage != null) VoicePhase.ERROR else VoicePhase.IDLE,
                errorMessage = errorMessage,
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
        /** How long model audio must stay quiet before SPEAKING settles to LISTENING.
         *  Longer than any intra-utterance streaming gap, far shorter than a turn. */
        const val QUIET_WINDOW_MS = 1_200L
    }
}
