package com.jarvis.wear

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.wear.compose.material.MaterialTheme
import androidx.wear.compose.material.Text
import com.jarvis.wear.ui.PairScreen
import kotlinx.coroutines.flow.Flow

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val tokenStore = (application as WearApp).tokenStore
        setContent { WearRoot(tokenStore.hasToken) }
    }
}

/**
 * Kök ekran (spec §5): [hasToken] Flow-tabanlı Compose state olarak izlenir —
 * [TokenListenerService][com.jarvis.wear.data.TokenListenerService] telefondan token'ı
 * yazdığı AN bu ekran kendiliğinden Sohbet'e geçer; yeniden başlatma ya da manuel
 * yenileme beklemez (4 Ağu overlay dersi sınıfı: state'i olay değil, akışı sür).
 */
@Composable
fun WearRoot(hasToken: Flow<Boolean>) {
    val hasTokenState by hasToken.collectAsState(initial = false)
    MaterialTheme {
        when (rootRoute(hasTokenState)) {
            Route.Pair -> PairScreen()
            Route.Chat -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("Sohbet hazır") // Task 5 gerçek Chat ekranıyla değişecek
            }
        }
    }
}
