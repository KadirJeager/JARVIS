package com.jarvis.data.auth

import android.util.Base64
import org.json.JSONObject

/**
 * Reads the `exp` claim out of a Google ID token.
 *
 * Why this exists: every call into Credential Manager with a nonce shows Play Services'
 * own "Oturumunuz açılıyor" sheet — a nonce deliberately defeats the credential cache,
 * which is the entire point of it (see [AuthManager]). Kadir saw that sheet on every
 * launch AND every voice dial, having said several times he did not want to.
 *
 * The way out is not to stop minting fresh tokens — that regression (a dead cached token
 * 401-ing everything an hour in) is exactly what the nonce fixed. It is to stop minting
 * one we do not need: an ID token states its own expiry, so we can answer "is the token
 * I already hold still good?" without asking anyone.
 *
 * The signature is NOT verified here, and must not be. This is not an authorisation
 * decision — the backend verifies every token on every request and remains the only
 * authority. This only asks "is it worth reusing", and the worst case of a wrong answer
 * is one extra mint or one 401 that [com.jarvis.data.net.TokenAuthenticator] already
 * retries.
 */
internal fun idTokenExpiryEpochSeconds(jwt: String?): Long? {
    if (jwt.isNullOrBlank()) return null
    val parts = jwt.split(".")
    if (parts.size < 2) return null
    return try {
        // URL_SAFE + NO_PADDING + NO_WRAP: JWT uses base64url and strips '=' padding.
        val payload = Base64.decode(parts[1], Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        val exp = JSONObject(String(payload, Charsets.UTF_8)).optLong("exp", 0L)
        exp.takeIf { it > 0L }
    } catch (e: Exception) {
        // A token we cannot read is a token we will not reuse -- fail towards minting a
        // fresh one, never towards trusting an unreadable string.
        null
    }
}

/**
 * True when [jwt] is still usable for at least [skewSeconds] more.
 *
 * The margin matters more than it looks: a token that expires mid-flight produces a 401
 * on a request that already left, and on the voice path that means a dialled call
 * failing rather than a retried GET. One minute of slack costs nothing (tokens live an
 * hour) and removes the whole class.
 */
internal fun isIdTokenFresh(jwt: String?, nowEpochSeconds: Long, skewSeconds: Long = 300L): Boolean {
    val exp = idTokenExpiryEpochSeconds(jwt) ?: return false
    return exp - nowEpochSeconds > skewSeconds
}
