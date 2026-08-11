package com.jarvis.data.voice.session

/**
 * Seam over the real microphone (`AudioRecord` in [AndroidMicSource]) so [VoiceSession]'s
 * frame-routing logic is JVM-testable with a fake. No Android import here on purpose.
 *
 * In protocol v2 the mic PCM stream is no longer the conversation channel (STT is
 * on-device); it keeps flowing to the server for speaker-ID only.
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
 * A source whose PCM frames can be tapped by ONE additional consumer without
 * disturbing the primary read path. The tap sees every frame the primary
 * consumer reads, BEFORE any gating the session applies -- parity with a
 * recognizer that owns its own microphone. JVM-pure on purpose.
 */
interface PcmTapSource {
    /**
     * Installs (or clears, with null) the single tap. Thread-safe.
     *
     * The tap must never block: it runs on the capture read path, so a slow or
     * suspending tap body directly delays the next PCM frame reaching the
     * primary consumer. Tap bodies must be a non-blocking operation (e.g. a
     * queue offer that drops on a full queue), never a suspend point.
     *
     * A throwing tap is caught by the implementation, logged once, and cleared --
     * it is never allowed to propagate out of the capture read path.
     */
    fun setTap(tap: ((ByteArray) -> Unit)?)
}
