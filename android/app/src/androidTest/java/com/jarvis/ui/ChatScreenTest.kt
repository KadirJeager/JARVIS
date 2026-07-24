package com.jarvis.ui

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.jarvis.data.chat.UiMessage
import com.jarvis.ui.chat.ChatScreen
import com.jarvis.ui.chat.ChatUiState
import com.jarvis.ui.theme.JarvisTheme
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

class ChatScreenTest {

    @get:Rule
    val rule = createComposeRule()

    @Test
    fun rendersBothBubbles_andForwardsInputAndSend() {
        var typed = ""
        var sent = false
        rule.setContent {
            JarvisTheme {
                ChatScreen(
                    state = ChatUiState(
                        messages = listOf(
                            UiMessage("user", "selam"),
                            UiMessage("model", "merhaba"),
                        ),
                    ),
                    onInput = { typed = it },
                    onSend = { sent = true },
                    onRetry = {},
                )
            }
        }

        rule.onNodeWithText("selam").assertIsDisplayed()
        rule.onNodeWithText("merhaba").assertIsDisplayed()

        rule.onNodeWithTag("chat_input").performTextInput("yeni")
        assertEquals("yeni", typed)

        rule.onNodeWithTag("send_button").performClick()
        assertTrue(sent)
    }
}
