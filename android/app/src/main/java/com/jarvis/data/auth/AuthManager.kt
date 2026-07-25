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
    suspend fun silentSignIn(): Result<String>
}

/**
 * Google sign-in via Credential Manager. Returns the Google **ID token** (JWT) that the
 * backend verifies as `Authorization: Bearer <token>`.
 *
 * - [signIn] is interactive (account picker) and needs an Activity [Context].
 * - [silentSignIn] re-auths without UI (single authorized account auto-selected) and may
 *   run from a background thread with the application context — used by the 401 retry.
 *
 * The last obtained token is cached in [currentToken] for the request interceptor.
 */
class AuthManager(private val appContext: Context) : AuthClient {

    private val credentialManager = CredentialManager.create(appContext)

    @Volatile
    private var token: String? = null

    override fun currentToken(): String? = token

    fun clear() {
        token = null
    }

    /** Interactive sign-in; [activityContext] must be an Activity to show the picker. */
    override suspend fun signIn(activityContext: Context): Result<String> =
        get(activityContext, filterByAuthorized = false, autoSelect = false)

    /** Silent re-auth (no UI); safe to call off the main thread with the app context. */
    override suspend fun silentSignIn(): Result<String> =
        get(appContext, filterByAuthorized = true, autoSelect = true)

    private suspend fun get(
        context: Context,
        filterByAuthorized: Boolean,
        autoSelect: Boolean,
    ): Result<String> = try {
        val option = GetGoogleIdOption.Builder()
            .setServerClientId(WEB_CLIENT_ID)
            .setFilterByAuthorizedAccounts(filterByAuthorized)
            .setAutoSelectEnabled(autoSelect)
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
