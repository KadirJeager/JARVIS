package com.jarvis.data.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import retrofit2.HttpException
import java.io.IOException

const val NETWORK_MESSAGE = "Bağlantı kurulamadı. İnterneti kontrol edip tekrar dene."

@Serializable
private data class ErrorBody(val detail: String? = null)

private val errorJson = Json { ignoreUnknownKeys = true }

/**
 * Turns a failed call into the message the user should read.
 *
 * The server owns every management rule AND its Turkish wording (spec §9): the
 * last-anchor guard, the manual-sample cap with its live numbers, the closed label
 * vocabulary. Re-deriving any of that here would put one rule in two places, and the
 * two would drift. So a 4xx body's `detail` is passed through VERBATIM; [fallback] only
 * covers responses that carry no detail at all.
 */
fun Throwable.userMessage(fallback: String): String = when (this) {
    is HttpException -> serverDetail() ?: fallback
    is IOException -> NETWORK_MESSAGE
    else -> fallback
}

private fun HttpException.serverDetail(): String? = try {
    // errorBody() is a one-shot stream; this is the only place that reads it.
    val raw = response()?.errorBody()?.string()
    if (raw.isNullOrBlank()) null
    else errorJson.decodeFromString<ErrorBody>(raw).detail?.takeIf { it.isNotBlank() }
} catch (_: Exception) {
    // A non-JSON body (an HTML gateway page, say) is not a reason to crash the screen.
    null
}
