package com.jarvis.data.chat

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import com.jarvis.data.jarvisDataStore
import java.util.UUID

private val SESSION_ID_KEY = stringPreferencesKey("session_id")

/**
 * Persists the CURRENT conversation's id in Preferences DataStore. The generate-if-absent
 * runs inside the [edit] transaction so concurrent first calls can't mint two ids.
 *
 * The DataStore delegate lives in [com.jarvis.data.jarvisDataStore] rather than here:
 * `preferencesDataStore(name = "jarvis")` may be declared only once per process, and a
 * second copy of that line throws at runtime the first time the other store is touched.
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
