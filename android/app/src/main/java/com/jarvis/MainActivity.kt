package com.jarvis

import android.os.Bundle
import androidx.activity.compose.setContent
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.rememberCoroutineScope
import androidx.fragment.app.FragmentActivity
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jarvis.ui.Nav
import com.jarvis.ui.chat.ChatViewModel
import com.jarvis.ui.theme.JarvisTheme
import kotlinx.coroutines.launch

class MainActivity : FragmentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val container = (application as JarvisApp).container
        setContent {
            JarvisTheme {
                val vm: ChatViewModel = viewModel { ChatViewModel(container.chatRepository) }
                val state by vm.state.collectAsState()
                val scope = rememberCoroutineScope()

                // Boot: try silent re-auth. Both outcomes must be reported -- the UI
                // stays on the boot splash until one of them lands, so swallowing the
                // failure would hang the app on the splash forever.
                LaunchedEffect(Unit) {
                    if (container.authManager.silentSignIn().isSuccess) vm.onSignedIn()
                    else vm.onSilentSignInFailed()
                }

                Nav(
                    state = state,
                    onSignIn = {
                        scope.launch {
                            vm.onSignInStarted()
                            val result = container.authManager.signIn(this@MainActivity)
                            // The failure branch used to be absent entirely, so a
                            // cancelled or failed credential flow left the button
                            // looking simply broken.
                            result.fold(
                                onSuccess = { vm.onSignedIn() },
                                onFailure = { vm.onSignInFailed(it.message) },
                            )
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
