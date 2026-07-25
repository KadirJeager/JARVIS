package com.jarvis.data.voice.session

import java.util.concurrent.TimeUnit
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

/**
 * Real WebSocket transport over OkHttp. Its own client, not [com.jarvis.data.net]'s: the
 * Bearer token travels INSIDE the hello TEXT frame (voice_protocol.py), not in a header,
 * so the auth interceptor/authenticator chain has no business here — and a voice call
 * legitimately goes minutes with no server frame, so read-timeout is disabled and a
 * 20s protocol ping is the liveness check instead (a dead TCP path then surfaces as
 * onFailure within one ping interval rather than never).
 *
 * Every OkHttp callback below arrives on OkHttp's own reader thread; forwarding straight
 * to [VoiceTransportListener] is the documented contract of that interface, and
 * [VoiceSession] is written to be called from any thread.
 */
class OkHttpVoiceTransport(
    private val client: OkHttpClient = OkHttpClient.Builder()
        .pingInterval(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build(),
) : VoiceTransport {

    @Volatile
    private var webSocket: WebSocket? = null

    override fun connect(url: String, listener: VoiceTransportListener) {
        webSocket = client.newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) = listener.onOpen()

                override fun onMessage(webSocket: WebSocket, text: String) = listener.onText(text)

                override fun onMessage(webSocket: WebSocket, bytes: ByteString) =
                    listener.onBinary(bytes.toByteArray())

                // Server started the close handshake: acknowledge, then onClosed fires.
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(code, reason)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) =
                    listener.onClosed()

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) =
                    listener.onFailure(t.message ?: t.javaClass.simpleName)
            },
        )
    }

    override fun sendText(text: String): Boolean = webSocket?.send(text) ?: false

    override fun sendBinary(bytes: ByteArray): Boolean =
        webSocket?.send(bytes.toByteString()) ?: false

    override fun close() {
        // 1000 = normal closure. null on a never-connected/already-closed socket is fine.
        webSocket?.close(1000, null)
        webSocket = null
    }
}
