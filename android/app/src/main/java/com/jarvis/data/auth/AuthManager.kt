package com.jarvis.data.auth

import android.content.Context
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialException
import androidx.credentials.exceptions.NoCredentialException
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential

/** Backend token audience — the web OAuth client id (brain/app/config.py OAUTH_CLIENT_ID). */
const val WEB_CLIENT_ID =
    "000000000000-tmu4im1mba53dmqj1gbhgba55v3i6hmb.apps.googleusercontent.com"

/**
 * The subset of [AuthManager] that `MainActivity` and `AppContainer` actually depend on,
 * extracted as a seam so an instrumented test can inject a fake and drive sign-in/token
 * state without Credential Manager or Google Play Services. [AuthManager]'s internals are
 * unchanged by this — it is a contract on top of the existing class, not a rewrite.
 */
interface AuthClient {
    fun currentToken(): String?
    suspend fun signIn(activityContext: Context): Result<String>
    /** [force] = mint a NEW token even if the cached one is still fresh; the 401
     *  retry path must, because a 401 means the cached token is bad regardless
     *  of what its `exp` claims. */
    suspend fun silentSignIn(force: Boolean = false): Result<String>
}

/**
 * Google sign-in via Credential Manager. Returns the Google **ID token** (JWT) that the
 * backend verifies as `Authorization: Bearer <token>`.
 *
 * - [signIn] is interactive (account picker) and needs an Activity [Context].
 * - [silentSignIn] reuses the token it already holds while that token is still fresh,
 *   and only calls Credential Manager when it is not. This is what keeps Play Services'
 *   "Oturumunuz açılıyor" sheet off the screen: the nonce below deliberately defeats the
 *   credential cache (that is why it fixes the stale-token lockout), so EVERY call into
 *   Credential Manager shows that sheet. Not calling it is the only way to not show it.
 *
 * The token is persisted (app-private DataStore, [TokenStore]) rather than kept only in
 * memory, because the case Kadir actually hits is a COLD start: an in-memory cache is
 * empty there and the sheet would still appear on every launch. An ID token lives about
 * an hour, so reopening the app inside that window now costs no UI at all.
 *
 * The persisted token is a bearer credential and is treated as one: app-private storage,
 * cleared by [clear]. It is the same secret the app already holds in memory and puts on
 * the wire with every request — persisting it widens the window, not the class.
 */
class AuthManager(
    private val appContext: Context,
    private val tokenStore: TokenStore = DataStoreTokenStore(appContext),
    private val nowEpochSeconds: () -> Long = { System.currentTimeMillis() / 1000 },
) : AuthClient {

    private val credentialManager = CredentialManager.create(appContext)

    @Volatile
    private var token: String? = null

    override fun currentToken(): String? = token

    fun clear() {
        token = null
    }

    /** Loads the persisted token, if any, so a cold start can skip Credential Manager. */
    private suspend fun cached(): String? {
        token?.let { return it }
        return tokenStore.read()?.also { token = it }
    }

    /** Interactive sign-in; [activityContext] must be an Activity to show the picker. */
    override suspend fun signIn(activityContext: Context): Result<String> =
        get(activityContext, filterByAuthorized = false, autoSelect = false)

    /** Silent re-auth; safe to call off the main thread with the app context.
     *
     *  Reuses the held token while it is still fresh -- no Credential Manager call, and
     *  therefore no "Oturumunuz açılıyor" sheet. `force` skips the reuse: the 401 retry
     *  must mint a new one, because a 401 means the token is bad whatever `exp` says. */
    override suspend fun silentSignIn(force: Boolean): Result<String> {
        if (!force) {
            val held = cached()
            if (isIdTokenFresh(held, nowEpochSeconds())) return Result.success(held!!)
        }
        return get(appContext, filterByAuthorized = true, autoSelect = true)
    }

    private suspend fun get(
        context: Context,
        filterByAuthorized: Boolean,
        autoSelect: Boolean,
    ): Result<String> = try {
        val option = GetGoogleIdOption.Builder()
            .setServerClientId(WEB_CLIENT_ID)
            .setFilterByAuthorizedAccounts(filterByAuthorized)
            .setAutoSelectEnabled(autoSelect)
            // Without a nonce, Play Services serves the SAME cached ID token until it
            // expires — a "silent re-sign-in" one hour into a session then returns a
            // DEAD token forever and every request 401s (saha, tablet, 26 Tem 2026
            // 03:30: chat and voice both down exactly 1h after first sign-in). The
            // nonce is embedded in the JWT, so a fresh random one forces a fresh mint
            // every time. The backend does not validate nonce; uniqueness is all it's
            // for here.
            .setNonce(java.util.UUID.randomUUID().toString())
            .build()
        val request = GetCredentialRequest.Builder()
            .addCredentialOption(option)
            .build()
        val response = credentialManager.getCredential(context, request)
        val credential = response.credential
        if (credential is CustomCredential &&
            credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
        ) {
            val idToken = GoogleIdTokenCredential.createFrom(credential.data).idToken
            token = idToken
            tokenStore.write(idToken)
            Result.success(idToken)
        } else {
            Result.failure(IllegalStateException("Beklenmeyen kimlik türü"))
        }
    } catch (e: NoCredentialException) {
        // No authorized account for silent flow -> caller may fall back to interactive.
        Result.failure(e)
    } catch (e: GetCredentialException) {
        Result.failure(e)
    }
}
