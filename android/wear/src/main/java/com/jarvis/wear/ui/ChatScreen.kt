package com.jarvis.wear.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.wear.compose.foundation.lazy.ScalingLazyColumn
import androidx.wear.compose.material.Button
import androidx.wear.compose.material.Card
import androidx.wear.compose.material.Chip
import androidx.wear.compose.material.CircularProgressIndicator
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text

/**
 * Sohbet ekranı (spec §5): üstte son soru, altında (varsa) cevap bir [Card] içinde, [busy]
 * iken küçük bir dönen gösterge, hata varsa kırmızı metin; altta [QUICK_COMMANDS] tek dokunuşluk
 * [Chip]leri -- hepsi aynı [ChatViewModel.send] ucundan geçer, düz metin girişiyle aynı yol.
 * En altta mikrofon [Button]u: Task 6 sesli girdiyi bağlayana kadar bilinçli olarak disabled --
 * yarım bir özelliği çalışıyormuş gibi göstermemek için buton görünür ama devre dışı.
 *
 * Yuvarlak kadranda okunabilirlik: sabit bir [androidx.compose.foundation.layout.Column] değil
 * [ScalingLazyColumn] -- uzun cevaplar kaydırılabilir, kenarlardaki öğeler otomatik küçülür.
 */
@Composable
fun ChatScreen(viewModel: ChatViewModel) {
    val state by viewModel.state.collectAsState()

    ScalingLazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 28.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        state.lastQuestion?.let { question ->
            item {
                Text(
                    text = question,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    color = MaterialTheme.colors.onSurfaceVariant,
                )
            }
        }
        if (state.busy) {
            item { CircularProgressIndicator(modifier = Modifier.size(20.dp)) }
        }
        state.lastReply?.let { reply ->
            item {
                Card(onClick = {}, modifier = Modifier.fillMaxWidth()) {
                    Text(reply)
                }
            }
        }
        state.error?.let { error ->
            item {
                Text(
                    text = error,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                    color = MaterialTheme.colors.error,
                )
            }
        }
        QUICK_COMMANDS.forEach { command ->
            item {
                Chip(
                    label = { Text(command) },
                    onClick = { viewModel.send(command) },
                    modifier = Modifier.fillMaxWidth(),
                    enabled = !state.busy,
                )
            }
        }
        item {
            // Task 6 burayı gerçek sesli girdiyle dolduracak; şimdilik disabled --
            // yarım bir özelliği çalışıyormuş gibi göstermemek için.
            Button(onClick = {}, enabled = false, modifier = Modifier.size(40.dp)) {
                Text("🎤")
            }
        }
    }
}
