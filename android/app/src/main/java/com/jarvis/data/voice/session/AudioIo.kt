package com.jarvis.data.voice.session

/**
 * Seam over the real microphone (`AudioRecord` in [AndroidMicSource]) so [VoiceSession]'s
 * frame-routing logic is JVM-testable with a fake. No Android import here on purpose.
 */
interface MicSource {
    /** Opens the input device at [sampleRateHz] and starts capturing. */
    fun start(sampleRateHz: Int)

    /**
     * Suspends until one frame of PCM16 mono audio is available, or returns null once
     * capture has been stopped (the caller's read loop should end on null, not retry).
     */
    suspend fun readFrame(): ByteArray?

    /** Stops capture and releases the input device. Safe to call even if never started. */
    fun stop()
}

/**
 * Seam over the real speaker (`AudioTrack` in [AndroidSpeakerSink]) so [VoiceSession]'s
 * frame-routing logic is JVM-testable with a fake. No Android import here on purpose.
 */
interface SpeakerSink {
    /** Opens the output device at [sampleRateHz] in streaming mode. */
    fun start(sampleRateHz: Int)

    /** Queues one chunk of PCM16 mono audio for playback. May block briefly if the
     *  device's internal buffer is full -- callers must not invoke this from the main
     *  thread (see [com.jarvis.data.voice.session.OkHttpVoiceTransport] callback thread). */
    fun write(pcm: ByteArray)

    /** Stops playback and releases the output device. Safe to call even if never started. */
    fun stop()
}
