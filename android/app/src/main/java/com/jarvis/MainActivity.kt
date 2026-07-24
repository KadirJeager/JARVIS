package com.jarvis

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jarvis.ui.Nav
import com.jarvis.ui.chat.ChatViewModel
import com.jarvis.ui.theme.JarvisTheme
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JarvisApp).container
        setContent {
            JarvisTheme {
                val vm: ChatViewModel = viewModel { ChatViewModel(container.chatRepository) }
                val state by vm.state.collectAsState()
                val scope = rememberCoroutineScope()

                // Boot: try silent re-auth; success flips to chat and loads history.
                LaunchedEffect(Unit) {
                    if (container.authManager.silentSignIn().isSuccess) vm.onSignedIn()
                }

                Nav(
                    state = state,
                    onSignIn = {
                        scope.launch {
                            if (container.authManager.signIn(this@MainActivity).isSuccess) {
                                vm.onSignedIn()
                            }
                        }
                    },
                    onInput = vm::onInputChange,
                    onSend = vm::send,
                    onRetry = vm::refreshHistory,
                )
            }
        }
    }
}
