package com.jarvis.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.jarvis.data.chat.Conversation
import com.jarvis.ui.theme.JarvisCyan
import com.jarvis.ui.theme.JarvisError
import com.jarvis.ui.theme.JarvisSurface
import com.jarvis.ui.theme.JarvisSurfaceHigh
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/**
 * The conversation list, as a panel over the chat.
 *
 * Hand-rolled rather than `ModalNavigationDrawer`: the open/closed fact already lives in
 * [ChatUiState], and a drawer keeps its own `DrawerState`, so the two would have to be
 * kept in sync — two sources of truth for one boolean, which is exactly the class of bug
 * that has cost this project the most.
 */
@Composable
fun ConversationsPanel(
    conversations: List<Conversation>,
    loading: Boolean,
    onNewConversation: () -> Unit,
    onOpen: (String) -> Unit,
    onDelete: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    Box(Modifier.fillMaxSize().testTag("conversations_panel")) {
        // Scrim: tapping outside closes, which is what every drawer on the platform does.
        Box(
            Modifier
                .fillMaxSize()
                .background(Color.Black.copy(alpha = 0.55f))
                .clickable(onClick = onDismiss)
                .testTag("conversations_scrim"),
        )
        Column(
            Modifier
                .fillMaxHeight()
                .width(300.dp)
                .background(JarvisSurface)
                .systemBarsPadding()
                .padding(vertical = 12.dp),
        ) {
            Text(
                "Sohbetler",
                style = MaterialTheme.typography.titleMedium,
                color = JarvisTextPrimary,
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp),
            )

            TextButton(
                onClick = onNewConversation,
                modifier = Modifier
                    .padding(horizontal = 12.dp)
                    .testTag("new_conversation"),
            ) {
                Text("＋  Yeni sohbet", color = JarvisCyan)
            }

            Spacer(Modifier.size(8.dp))

            when {
                loading -> Box(
                    Modifier.fillMaxWidth().padding(24.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator(color = JarvisCyan, modifier = Modifier.size(24.dp))
                }

                conversations.isEmpty() -> Text(
                    "Geçmiş sohbet yok.",
                    color = JarvisTextMuted,
                    style = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.padding(horizontal = 20.dp).testTag("conversations_empty"),
                )

                else -> LazyColumn(
                    contentPadding = PaddingValues(horizontal = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    items(conversations, key = { it.sessionId }) { row ->
                        ConversationRow(row, onOpen = onOpen, onDelete = onDelete)
                    }
                }
            }
        }
    }
}

/**
 * Deleting a conversation is irreversible and takes its messages with it, so it is two
 * taps: the row reveals its own confirm rather than offering a one-tap bin. The voice
 * screen learned this the hard way — a dense list of one-tap destructive actions is a
 * mis-tap waiting to happen.
 */
@Composable
private fun ConversationRow(
    row: Conversation,
    onOpen: (String) -> Unit,
    onDelete: (String) -> Unit,
) {
    var confirming by remember(row.sessionId) { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(if (confirming) JarvisSurfaceHigh else Color.Transparent)
            .testTag("conversation_${row.sessionId}"),
    ) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                row.title,
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextPrimary,
                maxLines = 1,
                modifier = Modifier
                    .weight(1f)
                    .clickable { onOpen(row.sessionId) }
                    .padding(vertical = 14.dp),
            )
            TextButton(
                onClick = { confirming = !confirming },
                modifier = Modifier.testTag("conversation_delete_${row.sessionId}"),
            ) {
                Text("×", color = JarvisTextMuted, style = MaterialTheme.typography.titleMedium)
            }
        }
        if (confirming) {
            Row(
                Modifier.fillMaxWidth().padding(start = 8.dp, bottom = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    "Bu sohbet ve mesajları silinsin mi?",
                    style = MaterialTheme.typography.bodySmall,
                    color = JarvisTextMuted,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = { confirming = false }) {
                    Text("Vazgeç", color = JarvisTextMuted)
                }
                TextButton(
                    onClick = { confirming = false; onDelete(row.sessionId) },
                    modifier = Modifier.testTag("conversation_delete_confirm_${row.sessionId}"),
                ) {
                    Text("Sil", color = JarvisError, fontWeight = FontWeight.SemiBold)
                }
            }
        }
    }
}
