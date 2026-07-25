package com.jarvis.data.chat

/**
 * Which conversation the chat is currently in.
 *
 * This used to hand out ONE id for the life of the install, which is why every launch
 * dropped Kadir back into the same endless thread. A conversation is now something you
 * start and switch between; the id is still the backend's `session_id`, so it must keep
 * matching `^[A-Za-z0-9._-]{1,128}$` (a bare UUID does).
 *
 * An interface so the repository stays unit-testable without Android DataStore — see
 * [DataStoreSessionStore] for the real implementation.
 */
interface SessionStore {
    /** The conversation in focus. Mints one if there has never been a conversation. */
    suspend fun sessionId(): String

    /**
     * Begin a NEW conversation and make it current; returns its id.
     *
     * Nothing is written server-side here: the backend creates a conversation summary
     * only when a message is appended, so a new conversation the user never types into
     * never appears in the list and costs nothing.
     */
    suspend fun startNew(): String

    /** Reopen an existing conversation (chosen from the list) and make it current. */
    suspend fun switchTo(sessionId: String)
}
