package com.jarvis.data.net

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.ResponseBody.Companion.toResponseBody
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import retrofit2.HttpException
import retrofit2.Response
import java.io.IOException

class ApiErrorTest {

    private fun httpError(code: Int, body: String): HttpException =
        HttpException(Response.error<Any>(code, body.toResponseBody("application/json".toMediaType())))

    /**
     * The server owns the rules AND their wording (spec §9). "Son çapa silinemez"
     * must reach the user verbatim; inventing a client-side equivalent would put the
     * same rule in two places.
     */
    @Test
    fun httpError_surfacesTheServersTurkishDetailVerbatim() {
        val message = httpError(
            400,
            """{"detail":"Son çapa silinemez: çapasız profil ses doğrulayamaz."}""",
        ).userMessage("olmadı")
        assertEquals("Son çapa silinemez: çapasız profil ses doğrulayamaz.", message)
    }

    @Test
    fun httpError_surfacesTheCapMessageWithItsNumbers() {
        val message = httpError(
            400,
            """{"detail":"Elle eklenen örnek sınırı dolu (5/5). Yenisini eklemek için önce elle eklenmiş bir örneği sil."}""",
        ).userMessage("olmadı")
        assertTrue(message.contains("(5/5)"))
    }

    @Test
    fun httpError_withoutADetailField_fallsBackToTheCallersMessage() {
        assertEquals("olmadı", httpError(500, """{"oops":1}""").userMessage("olmadı"))
    }

    @Test
    fun httpError_withUnparseableBody_fallsBackInsteadOfThrowing() {
        assertEquals("olmadı", httpError(502, "<html>gateway</html>").userMessage("olmadı"))
    }

    @Test
    fun ioError_reportsAConnectionProblem_notTheRawException() {
        val message = IOException("failed to connect").userMessage("olmadı")
        assertEquals(NETWORK_MESSAGE, message)
        assertTrue(message.contains("Bağlantı"))
    }

    @Test
    fun unknownError_usesTheFallback() {
        assertEquals("olmadı", IllegalStateException("boom").userMessage("olmadı"))
    }
}
