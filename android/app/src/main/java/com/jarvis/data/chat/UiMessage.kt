package com.jarvis.data.chat

/** A single chat bubble. [role] is "user" or "model" (matching the backend). */
data class UiMessage(val role: String, val text: String)
