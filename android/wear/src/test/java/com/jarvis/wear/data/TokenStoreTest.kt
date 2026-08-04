package com.jarvis.wear.data

import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Sözleşme: düz token depoya ASLA düz yazılmaz; cipher'dan geçer. */
class FakeCipher : TokenCipher {
    override fun encrypt(plain: String) = "enc(" + plain.reversed() + ")"
    override fun decrypt(blob: String) =
        if (blob.startsWith("enc(")) blob.removePrefix("enc(").removeSuffix(")").reversed() else null
}

class TokenStoreTest {

    private fun store(backing: FakePrefs = FakePrefs()) =
        Pair(TokenStore(backing, FakeCipher()), backing)

    @Test fun `save then read roundtrips through the cipher`() = runTest {
        val (s, backing) = store()
        s.save("jdt_gizli")
        assertEquals("jdt_gizli", s.read())
        // Depoda düz token YOK — yalnız cipher çıktısı var:
        assertFalse(backing.dump().contains("jdt_gizli"))
        assertTrue(backing.dump().contains("enc("))
    }

    @Test fun `read returns null when nothing saved`() = runTest {
        val (s, _) = store()
        assertNull(s.read())
    }

    @Test fun `clear removes the token and hasToken follows`() = runTest {
        val (s, _) = store()
        s.save("jdt_gizli")
        assertTrue(s.hasToken.first())
        s.clear()
        assertFalse(s.hasToken.first())
        assertNull(s.read())
    }

    @Test fun `corrupted blob reads as null not crash`() = runTest {
        val (s, backing) = store()
        backing.put(TokenStore.KEY_TOKEN, "bozuk-blob")
        assertNull(s.read())
    }
}
