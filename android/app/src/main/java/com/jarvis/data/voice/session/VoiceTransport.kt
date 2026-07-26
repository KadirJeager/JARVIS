package com.jarvis.data.voice.session

/**
 * Seam over the real WebSocket (OkHttp in [OkHttpVoiceTransport]) so [VoiceSession]'s
 * frame-routing logic is JVM-testable with a fake -- OkHttp's `WebSocket` cannot be
 * constructed without a live connection. No Android or OkHttp import here on purpose.
 */
interface VoiceTransport {
    /** Opens the connection to [url]; [listener] receives every subsequent callback. */
    fun connect(url: String, listener: VoiceTransportListener)

    /** Sends one TEXT frame (the hello, on this client). Returns false if not connected. */
    fun sendText(text: String): Boolean

    /** Sends one BINARY frame (one mic chunk). Returns false if not connected. */
    fun sendBinary(bytes: ByteArray): Boolean

    /** Closes the connection. Safe to call even if never connected or already closed. */
    fun close()
}

/**
 * Callbacks from [VoiceTransport]. Implementations (real or fake) invoke these on
 * whatever thread the underlying transport uses -- for the real OkHttp implementation
 * that is OkHttp's own reader thread, never the caller's thread and never Android's main
 * thread, so [VoiceSession] must not assume a particular thread here.
 */
interface VoiceTransportListener {
    fun onOpen()
    fun onText(text: String)
    fun onBinary(bytes: ByteArray)
    fun onClosed()
    fun onFailure(message: String)
}
