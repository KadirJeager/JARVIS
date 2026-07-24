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
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.jarvis.R
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisGradient
import com.jarvis.ui.theme.JarvisOnAccent
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/** Entry screen: the KJ brain mark, wordmark, and a single Google sign-in action. */
@Composable
fun SignInScreen(onSignIn: () -> Unit) {
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
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(28.dp))
                .background(JarvisGradient)
                .clickable(onClick = onSignIn)
                .padding(horizontal = 32.dp, vertical = 15.dp)
                .testTag("signin_button")
                .semantics { contentDescription = "Google ile giriş" },
            contentAlignment = Alignment.Center,
        ) {
            Text(
                "Google ile giriş",
                style = MaterialTheme.typography.labelLarge,
                color = JarvisOnAccent,
            )
        }
    }
}
