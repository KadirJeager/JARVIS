package com.jarvis.data.chat

/**
 * Supplies the persistent per-install session id. An interface so the repository can be
 * unit-tested without Android DataStore (see [DataStoreSessionStore] for the real impl).
 */
interface SessionStore {
    /** Stable id, matching the backend regex `^[A-Za-z0-9._-]{1,128}$` (a bare UUID does). */
    suspend fun sessionId(): String
}
