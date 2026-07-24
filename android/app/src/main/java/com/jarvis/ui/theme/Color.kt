package com.jarvis.ui.theme

import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color

// Palette derived from the KJ "digital brain" logo: near-black navy backdrop, a cyan
// left hemisphere (K) and a violet right hemisphere (J).
val JarvisBg = Color(0xFF0A0E1A)
val JarvisSurface = Color(0xFF151B2E)
val JarvisSurfaceHigh = Color(0xFF1E2740)
val JarvisCyan = Color(0xFF46D4E8)
val JarvisViolet = Color(0xFFA66BF5)
val JarvisTextPrimary = Color(0xFFEAEEF7)
val JarvisTextMuted = Color(0xFF8B93A8)
val JarvisOnAccent = Color(0xFF07131A)
val JarvisError = Color(0xFFFF6B7A)

/** The signature two-hemisphere accent: cyan (K) → violet (J). */
val JarvisGradient = Brush.linearGradient(listOf(JarvisCyan, JarvisViolet))
