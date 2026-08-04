package com.jarvis.wear.data

import org.junit.Assert.assertEquals
import org.junit.Test

/** [TokenListenerService.MESSAGE_PATH] must match the phone's
 * `com.jarvis.data.wear.WatchPairing.TOKEN_PATH` exactly ("/jarvis/device-token") —
 * but `:wear` and `:app` are separate, non-dependent Gradle modules (separate APKs,
 * separate devices), so they cannot share an actual Kotlin constant. This test pins
 * the wear-side literal so any accidental drift fails loudly here instead of silently
 * breaking pairing between two devices at runtime. */
class TokenListenerServiceTest {
    @Test fun `message path matches the phone's WatchPairing contract literal`() =
        assertEquals("/jarvis/device-token", TokenListenerService.MESSAGE_PATH)
}
