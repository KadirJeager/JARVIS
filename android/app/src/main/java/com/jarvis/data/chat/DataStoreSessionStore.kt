package com.jarvis.data.chat

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.util.UUID

private val Context.jarvisDataStore by preferencesDataStore(name = "jarvis")
private val SESSION_ID_KEY = stringPreferencesKey("session_id")

/**
 * Persists a per-install UUID in Preferences DataStore. The generate-if-absent runs
 * inside the [edit] transaction so concurrent first calls can't mint two ids.
 */
class DataStoreSessionStore(private val context: Context) : SessionStore {
    override suspend fun sessionId(): String {
        val prefs = context.jarvisDataStore.edit { p ->
            if (p[SESSION_ID_KEY] == null) {
                p[SESSION_ID_KEY] = UUID.randomUUID().toString()
            }
        }
        return prefs[SESSION_ID_KEY]!!
    }
}
