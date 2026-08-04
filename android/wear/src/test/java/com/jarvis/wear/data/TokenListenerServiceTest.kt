package com.jarvis.wear.data

import com.jarvis.wear.BuildConfig
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [TokenListenerService.MESSAGE_PATH] must match the phone's
 * `com.jarvis.data.wear.WatchPairing.TOKEN_PATH` exactly ("/jarvis/device-token") —
 * but `:wear` and `:app` are separate, non-dependent Gradle modules (separate APKs,
 * separate devices) and must not depend on each other directly, so a Kotlin constant
 * cannot be shared across them. Task 4 review fix: both modules' generated
 * `BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH` now read the SAME single literal from
 * `android/gradle.properties#jarvis.wearDeviceTokenMessagePath` (see each module's
 * `buildConfigField` in its `build.gradle.kts`) instead of each hand-typing its own copy
 * — see the equivalent pin in `WatchPairingTest`.
 *
 * Two things are pinned here, not one: (1) the shared literal itself still has the value
 * the phone expects (catches a wrong edit to gradle.properties), and (2) the service
 * actually reads that shared value rather than a separately hand-typed string (catches a
 * regression back to a local literal in [TokenListenerService]).
 */
class TokenListenerServiceTest {
    @Test fun `shared contract literal matches the phone's WatchPairing path`() =
        assertEquals("/jarvis/device-token", BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH)

    @Test fun `service matches on the shared contract, not a separately hand-typed literal`() =
        assertEquals(BuildConfig.WEAR_DEVICE_TOKEN_MESSAGE_PATH, TokenListenerService.MESSAGE_PATH)
}
