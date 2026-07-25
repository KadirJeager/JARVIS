package com.jarvis.data.auth

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import com.jarvis.data.jarvisDataStore
import kotlinx.coroutines.flow.first

/**
 * Remembers that this device has completed a sign-in at least once.
 *
 * This is NOT a credential and NOT an authorisation — it holds no token and grants
 * nothing. It answers one question at launch: "should we draw the chat immediately, or
 * do we genuinely not know who this is yet?" The real authority stays with Credential
 * Manager and, ultimately, with the backend, which verifies every request's Bearer token
 * regardless of what this flag says.
 */
interface AuthStateStore {
    suspend fun hasSignedInBefore(): Boolean
    suspend fun markSignedIn()
    suspend fun clearSignedIn()
}

private val SIGNED_IN_BEFORE = booleanPreferencesKey("signed_in_before")

class DataStoreAuthStateStore(context: Context) : AuthStateStore {

    private val appContext = context.applicationContext

    override suspend fun hasSignedInBefore(): Boolean =
        appContext.jarvisDataStore.data.first()[SIGNED_IN_BEFORE] == true

    override suspend fun markSignedIn() {
        appContext.jarvisDataStore.edit { it[SIGNED_IN_BEFORE] = true }
    }

    /** Called only when the credential is genuinely gone — see ChatViewModel. */
    override suspend fun clearSignedIn() {
        appContext.jarvisDataStore.edit { it[SIGNED_IN_BEFORE] = false }
    }
}
