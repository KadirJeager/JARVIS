package com.jarvis.ui.chat

import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.ConversationsRepository
import com.jarvis.data.chat.SessionStore
import com.jarvis.data.chat.UiMessage
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.HistoryMessage
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.ConversationDeletedResponse
import com.jarvis.data.net.ConversationDto
import com.jarvis.data.net.ConversationsApi
import com.jarvis.data.net.ConversationsResponse
import com.jarvis.data.net.JarvisApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {

    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    private class FakeSessionStore(private var id: String = "s") : SessionStore {
        override suspend fun sessionId(): String = id
        override suspend fun startNew(): String = "new".also { id = it }
        override suspend fun switchTo(sessionId: String) { id = sessionId }
    }

    private class FakeApi(
        var history: HistoryResponse = HistoryResponse(emptyList()),
        var reply: String = "ok",
        var failChat: Boolean = false,
        var failHistory: Boolean = false,
    ) : JarvisApi {
        override suspend fun chat(req: ChatRequest): ChatResponse {
            if (failChat) throw RuntimeException("boom")
            return ChatResponse(reply)
        }
        override suspend fun history(sessionId: String): HistoryResponse {
            if (failHistory) throw RuntimeException("boom")
            return history
        }
    }

    private fun vmWith(api: FakeApi) =
        ChatViewModel(ChatRepository(api, FakeSessionStore()))

    private class FakeConversationsApi(var rows: List<ConversationDto> = emptyList()) :
        ConversationsApi {
        var deleted: String? = null
        override suspend fun list() = ConversationsResponse(rows)
        override suspend fun delete(sessionId: String): ConversationDeletedResponse {
            deleted = sessionId
            return ConversationDeletedResponse(sessionId)
        }
    }

    private class CountingSessionStore(var current: String = "s-current") : SessionStore {
        var mints = 0
        override suspend fun sessionId() = current
        override suspend fun startNew(): String {
            mints++
            current = "new-$mints"
            return current
        }
        override suspend fun switchTo(sessionId: String) { current = sessionId }
    }

    private fun vmWithConversations(
        api: FakeApi = FakeApi(),
        convApi: FakeConversationsApi = FakeConversationsApi(),
        store: CountingSessionStore = CountingSessionStore(),
    ) = ChatViewModel(
        ChatRepository(api, store),
        ConversationsRepository(convApi, store),
    )

    /**
     * Kadir's complaint: the app "always starts from the very beginning of the
     * conversation". A launch must open a NEW one; the old ones live in the list.
     */
    @Test
    fun coldStart_opensANewConversation() = runTest(dispatcher) {
        val store = CountingSessionStore()
        val vm = vmWithConversations(store = store)

        vm.onColdStart()
        advanceUntilIdle()

        assertEquals(1, store.mints)
    }

    /**
     * The Activity's LaunchedEffect re-runs on every configuration change while the
     * ViewModel survives it, so a second call must be a no-op — otherwise rotating the
     * phone mid-chat would cut the conversation in half.
     */
    @Test
    fun coldStart_isIdempotent_soARotationDoesNotSplitTheConversation() = runTest(dispatcher) {
        val store = CountingSessionStore()
        val vm = vmWithConversations(store = store)

        vm.onColdStart()
        advanceUntilIdle()
        vm.onColdStart()
        advanceUntilIdle()

        assertEquals(1, store.mints)
    }

    @Test
    fun startingANewConversation_clearsTheThread_andClosesTheList() = runTest(dispatcher) {
        val vm = vmWithConversations(api = FakeApi(history = HistoryResponse(listOf(HistoryMessage("model", "eski", "t")))))
        vm.onSignedIn()
        advanceUntilIdle()
        assertEquals(1, vm.state.value.messages.size)

        vm.startNewConversation()
        advanceUntilIdle()

        assertTrue(vm.state.value.messages.isEmpty())
        assertFalse(vm.state.value.conversationsOpen)
    }

    @Test
    fun openingTheList_loadsIt() = runTest(dispatcher) {
        val convApi = FakeConversationsApi(listOf(ConversationDto("s1", "eski sohbet", "t", 2)))
        val vm = vmWithConversations(convApi = convApi)

        vm.toggleConversations()
        advanceUntilIdle()

        assertTrue(vm.state.value.conversationsOpen)
        assertEquals(1, vm.state.value.conversations.size)
        assertEquals("eski sohbet", vm.state.value.conversations[0].title)
    }

    @Test
    fun openingAConversation_switchesToItAndLoadsItsHistory() = runTest(dispatcher) {
        val store = CountingSessionStore()
        val vm = vmWithConversations(
            api = FakeApi(history = HistoryResponse(listOf(HistoryMessage("user", "eski mesaj", "t")))),
            store = store,
        )

        vm.openConversation("s-old")
        advanceUntilIdle()

        assertEquals("s-old", store.current)
        assertEquals(1, vm.state.value.messages.size)
        assertFalse(vm.state.value.conversationsOpen)
    }

    /** Deleting the conversation you are reading must clear the thread; deleting another
     *  must leave what is on screen alone. */
    @Test
    fun deletingTheCurrentConversation_clearsTheThread() = runTest(dispatcher) {
        val store = CountingSessionStore(current = "s-current")
        val convApi = FakeConversationsApi()
        val vm = vmWithConversations(
            api = FakeApi(history = HistoryResponse(listOf(HistoryMessage("model", "bir sey", "t")))),
            convApi = convApi,
            store = store,
        )
        vm.onSignedIn()
        advanceUntilIdle()

        vm.deleteConversation("s-current")
        advanceUntilIdle()

        assertEquals("s-current", convApi.deleted)
        assertTrue(vm.state.value.messages.isEmpty())
    }

    @Test
    fun deletingAnotherConversation_leavesTheOpenThreadAlone() = runTest(dispatcher) {
        val store = CountingSessionStore(current = "s-current")
        val vm = vmWithConversations(
            api = FakeApi(history = HistoryResponse(listOf(HistoryMessage("model", "bir sey", "t")))),
            store = store,
        )
        vm.onSignedIn()
        advanceUntilIdle()

        vm.deleteConversation("s-other")
        advanceUntilIdle()

        assertEquals(1, vm.state.value.messages.size)
    }

    @Test
    fun send_optimisticallyAppendsUser_thenModelReply() = runTest(dispatcher) {
        val vm = vmWith(FakeApi(reply = "cevap"))
        vm.onInputChange("selam")

        vm.send()
        // Synchronous optimistic state, before the coroutine runs.
        assertEquals(listOf(UiMessage("user", "selam")), vm.state.value.messages)
        assertTrue(vm.state.value.sending)
        assertEquals("", vm.state.value.input)

        advanceUntilIdle()
        assertEquals(
            listOf(UiMessage("user", "selam"), UiMessage("model", "cevap")),
            vm.state.value.messages,
        )
        assertFalse(vm.state.value.sending)
    }

    @Test
    fun send_failure_setsTurkishError_restoresInput_rollsBackBubble() = runTest(dispatcher) {
        val vm = vmWith(FakeApi(failChat = true))
        vm.onInputChange("selam")

        vm.send()
        advanceUntilIdle()

        assertFalse(vm.state.value.sending)
        assertNotNull(vm.state.value.error)
        assertTrue(vm.state.value.error!!.contains("Gönderilemedi"))
        assertEquals("selam", vm.state.value.input)          // typed input not lost
        assertTrue(vm.state.value.messages.isEmpty())        // optimistic bubble rolled back
    }

    @Test
    fun send_ignoresBlankInput() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onInputChange("   ")
        vm.send()
        advanceUntilIdle()
        assertTrue(vm.state.value.messages.isEmpty())
        assertFalse(vm.state.value.sending)
    }

    @Test
    fun refreshHistory_togglesLoading_andPopulates() = runTest(dispatcher) {
        val vm = vmWith(
            FakeApi(
                history = HistoryResponse(
                    listOf(HistoryMessage("user", "a", "t"), HistoryMessage("model", "b", "t")),
                ),
            ),
        )
        vm.refreshHistory()
        assertTrue(vm.state.value.loading)   // set synchronously
        advanceUntilIdle()
        assertFalse(vm.state.value.loading)
        assertEquals(2, vm.state.value.messages.size)
    }

    @Test
    fun onSignedIn_setsSignedIn_andLoadsHistory() = runTest(dispatcher) {
        val vm = vmWith(FakeApi(history = HistoryResponse(listOf(HistoryMessage("model", "hi", "t")))))
        vm.onSignedIn()
        advanceUntilIdle()
        assertTrue(vm.state.value.signedIn)
        assertEquals(AuthPhase.SIGNED_IN, vm.state.value.authPhase)
        assertEquals(1, vm.state.value.messages.size)
    }

    /**
     * The launch state is CHECKING, not signed-out. Nav shows the sign-in screen for
     * signed-out, and silentSignIn() is an async call that resolves after first
     * composition -- so a single boolean made every warm start flash "Google ile giriş"
     * at a user who was already authorized, which reads as "it forgot my session".
     */
    @Test
    fun startsInChecking_soAWarmStartNeverFlashesTheSignInScreen() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        assertEquals(AuthPhase.CHECKING, vm.state.value.authPhase)
        assertFalse(vm.state.value.signedIn)
    }

    @Test
    fun silentSignInFailure_movesToSignedOut() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onSilentSignInFailed()
        assertEquals(AuthPhase.SIGNED_OUT, vm.state.value.authPhase)
        assertNull(vm.state.value.error)      // not an error: nobody has signed in yet
    }

    /**
     * A user who has signed in before must land in the chat on the FIRST frame — no
     * spinner, no "oturum açılıyor".
     *
     * CHECKING exists for the launch where we genuinely don't know yet. But once we know
     * (the flag is persisted), blocking the UI on Credential Manager is a self-inflicted
     * wait: [com.jarvis.data.net.AuthInterceptor] already sends the request without a
     * header when no token is cached, and [com.jarvis.data.net.TokenAuthenticator]
     * refreshes on the resulting 401 and retries. So the token can arrive AFTER the chat
     * is on screen without anything breaking.
     */
    @Test
    fun returningUser_landsInChatOnTheFirstFrame_withNoSpinner() = runTest(dispatcher) {
        val vm = vmWith(FakeApi(history = HistoryResponse(listOf(HistoryMessage("model", "hi", "t")))))

        vm.onReturningUser()

        // Synchronous: asserted BEFORE advanceUntilIdle, i.e. this is the first frame.
        assertEquals(AuthPhase.SIGNED_IN, vm.state.value.authPhase)
        assertTrue(vm.state.value.signedIn)

        advanceUntilIdle()
        assertEquals(1, vm.state.value.messages.size)   // history still loads behind it
    }

    /**
     * The credential really is gone (account removed on the device). Only THEN may we
     * take the returning user back to the sign-in screen.
     */
    @Test
    fun returningUser_whoseCredentialIsGone_fallsBackToSignedOut() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onReturningUser()
        assertEquals(AuthPhase.SIGNED_IN, vm.state.value.authPhase)

        vm.onSilentSignInFailed()
        advanceUntilIdle()

        assertEquals(AuthPhase.SIGNED_OUT, vm.state.value.authPhase)
    }

    @Test
    fun interactiveSignIn_reportsProgress_thenSignsIn() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onSilentSignInFailed()
        vm.onSignInStarted()
        assertEquals(AuthPhase.SIGNING_IN, vm.state.value.authPhase)
        vm.onSignedIn()
        advanceUntilIdle()
        assertEquals(AuthPhase.SIGNED_IN, vm.state.value.authPhase)
    }

    /** A failed or cancelled credential flow used to be swallowed: the button simply
     *  did nothing. It must land back on the sign-in screen WITH a reason. */
    @Test
    fun interactiveSignInFailure_returnsToSignedOut_withATurkishMessage() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onSignInStarted()
        vm.onSignInFailed("no credential")
        assertEquals(AuthPhase.SIGNED_OUT, vm.state.value.authPhase)
        val error = vm.state.value.error
        assertNotNull(error)
        assertTrue(error!!.contains("Giriş yapılamadı"))
        assertTrue(error.contains("no credential"))
    }

    @Test
    fun retryingSignInClearsThePreviousFailure() = runTest(dispatcher) {
        val vm = vmWith(FakeApi())
        vm.onSignInFailed("boom")
        assertNotNull(vm.state.value.error)
        vm.onSignInStarted()
        assertNull(vm.state.value.error)
    }
}
