package com.jarvis.data.auth

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import com.jarvis.data.jarvisDataStore
import kotlinx.coroutines.flow.first

/**
 * Holds the last Google ID token across process restarts.
 *
 * Unlike [AuthStateStore] this DOES hold a credential, and the distinction is the point:
 * that one answers "have we ever signed in", this one carries the bearer token itself. It
 * exists because the sheet Kadir kept seeing appears on COLD start, where an in-memory
 * cache is empty by definition — see [AuthManager] for why not calling Credential Manager
 * is the only way to not show it.
 *
 * It grants nothing on its own: the backend verifies every token on every request, and an
 * expired one simply fails and is replaced. Storage is the app-private DataStore, the
 * same file the rest of the app's preferences live in.
 */
interface TokenStore {
    suspend fun read(): String?
    suspend fun write(token: String)
    suspend fun clear()
}

private val ID_TOKEN = stringPreferencesKey("id_token")

class DataStoreTokenStore(context: Context) : TokenStore {

    private val appContext = context.applicationContext

    override suspend fun read(): String? =
        appContext.jarvisDataStore.data.first()[ID_TOKEN]?.takeIf { it.isNotBlank() }

    override suspend fun write(token: String) {
        appContext.jarvisDataStore.edit { it[ID_TOKEN] = token }
    }

    override suspend fun clear() {
        appContext.jarvisDataStore.edit { it.remove(ID_TOKEN) }
    }
}
