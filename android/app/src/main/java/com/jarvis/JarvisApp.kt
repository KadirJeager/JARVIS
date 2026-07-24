package com.jarvis

import android.app.Application
import android.content.Context
import com.jarvis.data.auth.AuthManager
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.DataStoreSessionStore
import com.jarvis.data.net.JarvisApi
import com.jarvis.data.net.NetworkModule
import kotlinx.coroutines.runBlocking

/**
 * Hand-rolled DI container (no Hilt — YAGNI for this slice). Wires the deployed backend
 * client with the auth token provider and the 401 silent-refresh path.
 */
class AppContainer(context: Context) {
    private val appContext = context.applicationContext

    val authManager = AuthManager(appContext)

    private val api: JarvisApi = NetworkModule.create(
        tokenProvider = { authManager.currentToken() },
        // Runs on OkHttp's background thread, so blocking here is fine.
        tokenRefresher = { runBlocking { authManager.silentSignIn().getOrNull() } },
    )

    val chatRepository = ChatRepository(api, DataStoreSessionStore(appContext))
}

class JarvisApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}
