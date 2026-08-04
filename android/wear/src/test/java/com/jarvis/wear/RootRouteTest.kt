package com.jarvis.wear

import org.junit.Assert.assertEquals
import org.junit.Test

/** Dürüst ekran kuralı (spec §5): token yoksa sessiz boş ekran DEĞİL,
 * "Telefondan eşleştir" ekranı. Ama DataStore'un ilk cevabı gelene kadar (henüz
 * "bilmiyorum" durumu) da Pair'e DÜŞMEZ — [Route.Unknown]'da bekler (Task 4 review fix:
 * cold-start'ta zaten eşleştirilmiş bir saatte bile görülen yanlış-ekran flaşı). */
class RootRouteTest {
    @Test fun `unknown token state routes to a neutral screen, not pair`() =
        assertEquals(Route.Unknown, rootRoute(null))
    @Test fun `no token routes to pair screen`() = assertEquals(Route.Pair, rootRoute(false))
    @Test fun `token routes to chat`() = assertEquals(Route.Chat, rootRoute(true))
}
