package com.jarvis.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val JarvisColorScheme = darkColorScheme(
    primary = JarvisCyan,
    onPrimary = JarvisOnAccent,
    secondary = JarvisViolet,
    onSecondary = JarvisOnAccent,
    background = JarvisBg,
    onBackground = JarvisTextPrimary,
    surface = JarvisSurface,
    onSurface = JarvisTextPrimary,
    surfaceVariant = JarvisSurfaceHigh,
    onSurfaceVariant = JarvisTextMuted,
    error = JarvisError,
)

/** Dark-first Jarvis theme built from the KJ logo palette. */
@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = JarvisColorScheme,
        typography = JarvisTypography,
        content = content,
    )
}
