package com.jarvis.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable

/**
 * Minimal Material3 wrapper. The full Jarvis palette/typography arrives in Task 6
 * (frontend-design); for now this just establishes the theming seam.
 */
@Composable
fun JarvisTheme(content: @Composable () -> Unit) {
    MaterialTheme(content = content)
}
