package com.jarvis.ui.chat

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Popup
import com.jarvis.R
import com.jarvis.data.chat.UiMessage
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisGradient
import com.jarvis.ui.theme.JarvisOnAccent
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/**
 * Single immersive chat thread (Gemini-referenced): user bubbles right in the signature
 * cyan→violet gradient, model bubbles left on a flat surface. Stateless — all state is
 * hoisted to [ChatViewModel].
 */
@Composable
fun ChatScreen(
    state: ChatUiState,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
    onRetry: () -> Unit,
    onOpenVoiceProfile: () -> Unit = {},
    onToggleConversations: () -> Unit = {},
    onNewConversation: () -> Unit = {},
    onOpenConversation: (String) -> Unit = {},
    onDeleteConversation: (String) -> Unit = {},
) {
    Column(
        Modifier
            .fillMaxSize()
            .background(JarvisBg)
            .systemBarsPadding()
            .imePadding(),
    ) {
        TopBar(onOpenVoiceProfile, onToggleConversations)
        Box(Modifier.weight(1f).fillMaxWidth()) {
            if (state.messages.isEmpty() && !state.loading) {
                EmptyHint()
            } else {
                LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(state.messages) { Bubble(it) }
                }
            }
            if (state.loading) {
                CircularProgressIndicator(
                    color = JarvisCyan,
                    modifier = Modifier.align(Alignment.TopCenter).padding(top = 16.dp).size(28.dp),
                )
            }
        }
        state.error?.let { ErrorBanner(it, onRetry) }
        InputBar(input = state.input, sending = state.sending, onInput = onInput, onSend = onSend)

        // Popup, not a sibling Box: an overlay that participates in this Column's layout
        // changes its geometry, and with imePadding on the Column that pushed the newest
        // messages off the top of the screen the moment the keyboard opened. A Popup
        // renders in its own window and occupies no space here.
        if (state.conversationsOpen) {
            Popup(onDismissRequest = onToggleConversations) {
                ConversationsPanel(
                    conversations = state.conversations,
                    loading = state.conversationsLoading,
                    onNewConversation = onNewConversation,
                    onOpen = onOpenConversation,
                    onDelete = onDeleteConversation,
                    onDismiss = onToggleConversations,
                )
            }
        }
    }
}

@Composable
private fun TopBar(onOpenVoiceProfile: () -> Unit, onToggleConversations: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextButton(
            onClick = onToggleConversations,
            modifier = Modifier.testTag("open_conversations"),
        ) {
            Text("☰", color = JarvisTextPrimary, style = MaterialTheme.typography.titleLarge)
        }
        Spacer(Modifier.size(2.dp))
        Image(
            painter = painterResource(R.drawable.logo_kj),
            contentDescription = null,
            modifier = Modifier.size(30.dp).clip(RoundedCornerShape(8.dp)),
        )
        Spacer(Modifier.size(10.dp))
        Text("Jarvis", style = MaterialTheme.typography.titleLarge, color = JarvisTextPrimary)
        Spacer(Modifier.weight(1f))
        TextButton(
            onClick = onOpenVoiceProfile,
            // No extra .semantics{contentDescription=...}: the child Text already
            // supplies that label, and Compose merges semantics for a clickable.
            modifier = Modifier.testTag("open_voice_profile"),
        ) {
            Text("Ses kimliğim", color = JarvisCyan, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

@Composable
private fun Bubble(message: UiMessage) {
    val isUser = message.role == "user"
    Row(
        Modifier.fillMaxWidth(),
        horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start,
    ) {
        val shape = RoundedCornerShape(
            topStart = 18.dp,
            topEnd = 18.dp,
            bottomStart = if (isUser) 18.dp else 4.dp,
            bottomEnd = if (isUser) 4.dp else 18.dp,
        )
        val bubbleModifier = Modifier
            .widthIn(max = 300.dp)
            .clip(shape)
            .then(
                if (isUser) Modifier.background(JarvisGradient)
                else Modifier.background(JarvisSurface),
            )
            .padding(horizontal = 16.dp, vertical = 11.dp)
        Text(
            text = message.text,
            color = if (isUser) JarvisOnAccent else JarvisTextPrimary,
            style = MaterialTheme.typography.bodyLarge,
            modifier = bubbleModifier,
        )
    }
}

@Composable
private fun EmptyHint() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(
            "Bir şey sor, başlayalım.",
            color = JarvisTextMuted,
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

@Composable
private fun ErrorBanner(message: String, onRetry: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            message,
            color = JarvisError,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onRetry) {
            Text("Tekrar dene", color = JarvisCyan)
        }
    }
}

@Composable
private fun InputBar(
    input: String,
    sending: Boolean,
    onInput: (String) -> Unit,
    onSend: () -> Unit,
) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        OutlinedTextField(
            value = input,
            onValueChange = onInput,
            modifier = Modifier.weight(1f).testTag("chat_input"),
            placeholder = { Text("Mesaj yaz", color = JarvisTextMuted) },
            shape = RoundedCornerShape(24.dp),
            maxLines = 5,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { onSend() }),
            colors = TextFieldDefaults.colors(
                focusedContainerColor = JarvisSurface,
                unfocusedContainerColor = JarvisSurface,
                focusedTextColor = JarvisTextPrimary,
                unfocusedTextColor = JarvisTextPrimary,
                cursorColor = JarvisCyan,
                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
            ),
        )
        Spacer(Modifier.size(8.dp))
        IconButton(
            onClick = onSend,
            enabled = !sending,
            modifier = Modifier
                .size(48.dp)
                .clip(RoundedCornerShape(24.dp))
                .background(JarvisGradient)
                .testTag("send_button")
                .semantics { contentDescription = "Gönder" },
        ) {
            Text("↑", color = JarvisOnAccent, style = MaterialTheme.typography.titleLarge)
        }
    }
}
