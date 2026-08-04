package com.jarvis.wear.ui

import android.app.Activity
import android.content.Intent
import android.speech.RecognizerIntent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable

/** Sistem konuşma tanıma: ses kaydı sunucuya GİTMEZ (spec §2/C — v1'de saat
 * kanalında ses kanıtı yok; kırmızı bölge onay kartlarının arkasında). */
@Composable
fun rememberSpeechLauncher(onResult: (String) -> Unit) =
    rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data
                ?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                ?.firstOrNull()
                ?.let(onResult)
        }
    }

fun speechIntent(): Intent =
    Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(
            RecognizerIntent.EXTRA_LANGUAGE_MODEL,
            RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
        )
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "tr-TR")
        putExtra(RecognizerIntent.EXTRA_PROMPT, "Jarvis dinliyor")
    }

/** TTS tekrar koruması — compose yeniden çizimlerinde kekeleme yok. Her yeni
 * `send()` çağrısında [ChatState.lastReply] aynı değerde kalabilir (busy geçişi
 * dolayısıyla `LaunchedEffect` yeniden tetiklenmez), ama recomposition'lar cevabı
 * ikinci kez [speak]'e YOLLAMAMALI -- [last] o tekrarı burada keser. */
class ReplySpeaker(private val speak: (String) -> Unit) {
    private var last: String? = null
    fun speakIfNew(reply: String?) {
        if (reply != null && reply != last) {
            last = reply
            speak(reply)
        }
    }
}
