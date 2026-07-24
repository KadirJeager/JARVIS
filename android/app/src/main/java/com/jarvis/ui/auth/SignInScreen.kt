package com.jarvis.ui.auth

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.jarvis.R
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisGradient
import com.jarvis.ui.theme.JarvisOnAccent
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/**
 * Entry screen: the KJ brain mark, wordmark, and a single Google sign-in action.
 *
 * [signingIn] makes the credential flow visible — it can take seconds behind a system
 * sheet, and without this the button looked inert. [error] surfaces a failed or
 * cancelled flow, which used to be swallowed entirely: the tap simply did nothing.
 */
@Composable
fun SignInScreen(
    onSignIn: () -> Unit,
    signingIn: Boolean = false,
    error: String? = null,
) {
    Column(
        Modifier
            .fillMaxSize()
            .background(JarvisBg)
            .systemBarsPadding()
            .padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Image(
            painter = painterResource(R.drawable.logo_kj),
            contentDescription = null,
            modifier = Modifier.size(120.dp).clip(RoundedCornerShape(28.dp)),
        )
        Spacer(Modifier.size(28.dp))
        Text("Jarvis", style = MaterialTheme.typography.titleLarge, color = JarvisTextPrimary)
        Spacer(Modifier.size(8.dp))
        Text(
            "Kişisel asistanın",
            style = MaterialTheme.typography.bodyLarge,
            color = JarvisTextMuted,
        )
        Spacer(Modifier.size(44.dp))
        val label = if (signingIn) "Giriş yapılıyor…" else "Google ile giriş"
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(28.dp))
                .background(JarvisGradient)
                // Inert while the system credential sheet is up: a second tap would
                // start a second flow behind the first.
                .clickable(enabled = !signingIn, onClick = onSignIn)
                .alpha(if (signingIn) 0.6f else 1f)
                .padding(horizontal = 32.dp, vertical = 15.dp)
                .testTag("signin_button")
                .semantics { contentDescription = label },
            contentAlignment = Alignment.Center,
        ) {
            Text(label, style = MaterialTheme.typography.labelLarge, color = JarvisOnAccent)
        }
        if (error != null) {
            Spacer(Modifier.size(20.dp))
            Text(
                error,
                style = MaterialTheme.typography.bodyMedium,
                color = JarvisTextMuted,
                textAlign = TextAlign.Center,
                modifier = Modifier.testTag("signin_error"),
            )
        }
    }
}
