package com.jarvis.data.wear

import com.jarvis.BuildConfig
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** MessageClient ve API, arayüz arkasına alınır: JVM testi gerçek Play Services
 * istemez. Sözleşme: (1) önce mint, (2) TÜM bağlı düğümlere gönder, (3) düğüm
 * yoksa mint bile YAPILMAZ (boşa token basılmaz), (4) hata Türkçe mesajla döner. */
class WatchPairingTest {

    private class FakeMinter(var result: Result<String> = Result.success("jdt_yeni")) {
        var calls = 0
        suspend fun mint(device: String): String { calls++; return result.getOrThrow() }
    }

    private class FakeNodes(var nodes: List<String> = listOf("node1")) {
        val sent = mutableListOf<Pair<String, String>>()   // (nodeId, payload)
        suspend fun connectedNodes() = nodes
        suspend fun send(nodeId: String, path: String, payload: ByteArray) {
            require(path == WatchPairing.TOKEN_PATH)
            sent += nodeId to String(payload, Charsets.UTF_8)
        }
    }

    private fun pairing(minter: FakeMinter, nodes: FakeNodes) =
        WatchPairing(mint = minter::mint, listNodes = nodes::connectedNodes, sendTo = nodes::send)

    @Test fun `pairs by minting once and sending to every node`() = runTest {
        val minter = FakeMinter(); val nodes = FakeNodes(listOf("a", "b"))
        val result = pairing(minter, nodes).pair()
        assertEquals(PairResult.Sent(2), result)
        assertEquals(1, minter.calls)
        assertEquals(listOf("a" to "jdt_yeni", "b" to "jdt_yeni"), nodes.sent)
    }

    @Test fun `no connected watch means no mint at all`() = runTest {
        val minter = FakeMinter(); val nodes = FakeNodes(emptyList())
        assertEquals(PairResult.NoWatch, pairing(minter, nodes).pair())
        assertEquals(0, minter.calls)
    }

    @Test fun `mint failure surfaces a Turkish message`() = runTest {
        val minter = FakeMinter(Result.failure(RuntimeException("500")))
        val result = pairing(minter, FakeNodes()).pair()
        assertTrue(result is PairResult.Failed)
        assertTrue((result as PairResult.Failed).userMessage.isNotBlank())
    }

    /** Task 4 review fix: [WatchPairing.TOKEN_PATH] must read the SAME shared
     * BuildConfig source `:wear`'s `TokenListenerService.MESSAGE_PATH` reads (both
     * generated from `android/gradle.properties#jarvis.wearDeviceTokenMessagePath`) —
     * not a locally hand-typed literal that could silently drift from the watch side. */
    @Test fun `token path is sourced from the shared BuildConfig contract`() =
        assertEquals(BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH, WatchPairing.TOKEN_PATH)
}
