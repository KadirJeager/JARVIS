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
