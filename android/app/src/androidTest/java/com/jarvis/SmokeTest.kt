package com.jarvis

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.rule.GrantPermissionRule
import org.junit.Rule
import org.junit.Test

class SmokeTest {

    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    // POST_NOTIFICATIONS is now requested at STARTUP (approvals reach Kadir by push,
    // North Star §4.8), not only inside startVoice(). Without this grant the system
    // dialog opens over every MainActivity launch here and deadlocks the whole class.
    @get:Rule
    val notificationPermission: GrantPermissionRule =
        GrantPermissionRule.grant(android.Manifest.permission.POST_NOTIFICATIONS)


    @Test
    fun showsJarvis() {
        composeRule.onNodeWithText("Jarvis").assertIsDisplayed()
    }
}
