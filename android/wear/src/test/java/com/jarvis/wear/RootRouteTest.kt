package com.jarvis.wear

import org.junit.Assert.assertEquals
import org.junit.Test

/** Dürüst ekran kuralı (spec §5): token yoksa sessiz boş ekran DEĞİL,
 * "Telefondan eşleştir" ekranı. */
class RootRouteTest {
    @Test fun `no token routes to pair screen`() = assertEquals(Route.Pair, rootRoute(false))
    @Test fun `token routes to chat`() = assertEquals(Route.Chat, rootRoute(true))
}
