package com.jarvis.wear.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.wear.compose.material.Text

/** Dürüst durum ekranı (spec §5): token yokken sessiz boş ekran yerine bunu göster. */
@Composable
fun PairScreen() {
    Column(
        modifier = Modifier.fillMaxSize().padding(16.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Saat eşleştirilmemiş", textAlign = TextAlign.Center)
        Spacer(Modifier.height(8.dp))
        Text(
            "Telefonda Jarvis'i aç ve menüden 'Saati eşleştir'e dokun.",
            textAlign = TextAlign.Center,
        )
    }
}
