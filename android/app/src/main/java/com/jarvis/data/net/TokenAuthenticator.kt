package com.jarvis.data.net

import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route

/**
 * On a 401, obtains a fresh token via [refreshToken] (a blocking silent re-auth) and
 * retries the request once with it. Gives up (returns null) if the refresh yields no
 * token or a retry has already happened — `response.priorResponse != null` bounds it to
 * a single retry, preventing an auth loop.
 *
 * Runs on an OkHttp background thread, so [refreshToken] may block (e.g. runBlocking
 * around AuthManager.silentSignIn()).
 */
class TokenAuthenticator(private val refreshToken: () -> String?) : Authenticator {
    override fun authenticate(route: Route?, response: Response): Request? {
        if (response.priorResponse != null) return null // already retried once
        val newToken = refreshToken() ?: return null
        return response.request.newBuilder()
            .header("Authorization", "Bearer $newToken")
            .build()
    }
}
