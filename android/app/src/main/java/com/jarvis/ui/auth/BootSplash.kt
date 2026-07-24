package com.jarvis.ui.auth

import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.res.painterResource
import com.jarvis.R
import com.jarvis.ui.theme.JarvisBg
import com.jarvis.ui.theme.JarvisTextMuted
import com.jarvis.ui.theme.JarvisTextPrimary

/**
 * Shown while silent re-auth is still in flight.
 *
 * Deliberately carries NO sign-in action. The returning user — which is every launch
 * after the first — is already authorized, so offering "Google ile giriş" here would
 * flash a login prompt at someone who never logged out. This screen says "hold on",
 * not "log in", and it resolves into either the chat or the real sign-in screen.
 */
@Composable
fun BootSplash() {
    Column(
        Modifier
            .fillMaxSize()
            .background(JarvisBg)
            .padding(32.dp)
            .testTag("boot_splash"),
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
            "Oturum kontrol ediliyor…",
            style = MaterialTheme.typography.bodyLarge,
            color = JarvisTextMuted,
        )
    }
}
