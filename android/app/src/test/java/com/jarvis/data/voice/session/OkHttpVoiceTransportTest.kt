package com.jarvis.data.voice.session

import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Drives the REAL OkHttp WebSocket implementation against a local MockWebServer upgrade —
 * same rationale as VoicePatchWireTest: VoiceSessionTest proves the state machine against
 * a fake transport, so only this test would notice the real transport mangling a frame,
 * dropping the binary/text distinction, or failing to surface a refused connection.
 */
class OkHttpVoiceTransportTest {

    /** Server side of the socket: records what the client actually sent. */
    private class ServerSide : WebSocketListener() {
        val texts = LinkedBlockingQueue<String>()
        val binaries = LinkedBlockingQueue<ByteString>()
        @Volatile var socket: WebSocket? = null
        val opened = CountDownLatch(1)

        override fun onOpen(webSocket: WebSocket, response: Response) {
            socket = webSocket
            opened.countDown()
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            texts.add(text)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            binaries.add(bytes)
        }

        // Without this, a client-initiated close never completes its handshake (the
        // default listener ignores onClosing), the socket stays half-open, and
        // MockWebServer.close() in tearDown times out with "Gave up waiting for queue".
        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(code, reason)
        }
    }

    /** Client-side listener that records every callback for assertion. */
    private class RecordingListener : VoiceTransportListener {
        val opened = CountDownLatch(1)
        val closed = CountDownLatch(1)
        val failed = CountDownLatch(1)
        val texts = LinkedBlockingQueue<String>()
        val binaries = LinkedBlockingQueue<ByteArray>()
        @Volatile var failureMessage: String? = null

        override fun onOpen() = opened.countDown()
        override fun onText(text: String) { texts.add(text) }
        override fun onBinary(bytes: ByteArray) { binaries.add(bytes) }
        override fun onClosed() = closed.countDown()
        override fun onFailure(message: String) {
            failureMessage = message
            failed.countDown()
        }
    }

    private lateinit var server: MockWebServer
    private lateinit var serverSide: ServerSide
    private lateinit var transport: OkHttpVoiceTransport
    private lateinit var listener: RecordingListener

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        serverSide = ServerSide()
        transport = OkHttpVoiceTransport()
        listener = RecordingListener()
    }

    @After
    fun tearDown() {
        transport.close()
        server.close()
    }

    private fun upgrade() {
        server.enqueue(MockResponse.Builder().webSocketUpgrade(serverSide).build())
    }

    private fun connectAndAwaitOpen() {
        upgrade()
        transport.connect(server.url("/ws/voice").toString(), listener)
        assertTrue("client onOpen never fired", listener.opened.await(5, TimeUnit.SECONDS))
        assertTrue("server never saw the socket open", serverSide.opened.await(5, TimeUnit.SECONDS))
    }

    @Test
    fun connect_firesOnOpen() {
        connectAndAwaitOpen()
    }

    @Test
    fun sendText_arrivesAtServerAsTextFrame() {
        connectAndAwaitOpen()
        assertTrue(transport.sendText("""{"token":"t"}"""))
        assertEquals("""{"token":"t"}""", serverSide.texts.poll(5, TimeUnit.SECONDS))
    }

    @Test
    fun sendBinary_arrivesAtServerAsBinaryFrame_bytesIntact() {
        connectAndAwaitOpen()
        val pcm = byteArrayOf(0, 1, -2, 127, -128, 64)
        assertTrue(transport.sendBinary(pcm))
        val received = serverSide.binaries.poll(5, TimeUnit.SECONDS)
        assertEquals(ByteString.of(*pcm), received)
        // A text frame must NOT have been fabricated out of the binary payload.
        assertTrue(serverSide.texts.isEmpty())
    }

    @Test
    fun serverText_reachesListenerAsText() {
        connectAndAwaitOpen()
        serverSide.socket!!.send("""{"type":"turn_complete"}""")
        assertEquals("""{"type":"turn_complete"}""", listener.texts.poll(5, TimeUnit.SECONDS))
    }

    @Test
    fun serverBinary_reachesListenerAsBytesIntact() {
        connectAndAwaitOpen()
        val pcm = byteArrayOf(9, 8, -7, 0, 33)
        serverSide.socket!!.send(ByteString.of(*pcm))
        val received = listener.binaries.poll(5, TimeUnit.SECONDS)
        assertEquals(ByteString.of(*pcm), ByteString.of(*received!!))
    }

    @Test
    fun serverInitiatedClose_firesOnClosed() {
        connectAndAwaitOpen()
        serverSide.socket!!.close(1000, "done")
        assertTrue("onClosed never fired", listener.closed.await(5, TimeUnit.SECONDS))
    }

    @Test
    fun connectionRefused_firesOnFailure() {
        val url = server.url("/ws/voice").toString()
        // Shut the server down so the TCP connection itself is refused.
        server.close()
        transport.connect(url, listener)
        assertTrue("onFailure never fired", listener.failed.await(5, TimeUnit.SECONDS))
        assertTrue(listener.failureMessage != null)
    }

    @Test
    fun sendBeforeConnect_returnsFalse() {
        assertFalse(transport.sendText("hello"))
        assertFalse(transport.sendBinary(byteArrayOf(1)))
    }

    @Test
    fun closeWithoutConnect_isSafe() {
        transport.close() // must not throw
    }

    @Test
    fun closeAfterConnect_isSafe_andSendAfterCloseReturnsFalse() {
        connectAndAwaitOpen()
        transport.close()
        // OkHttp queues sends on an already-closing socket as a rejection: false.
        assertFalse(transport.sendText("late"))
    }
}
