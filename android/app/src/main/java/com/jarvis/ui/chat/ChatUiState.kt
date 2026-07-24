package com.jarvis.ui.chat

import com.jarvis.data.chat.UiMessage

/** Immutable UI state for the chat screen. */
data class ChatUiState(
    val messages: List<UiMessage> = emptyList(),
    val input: String = "",
    val sending: Boolean = false,
    val loading: Boolean = false,
    val error: String? = null,
    val signedIn: Boolean = false,
)
