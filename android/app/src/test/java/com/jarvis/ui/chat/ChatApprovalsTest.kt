package com.jarvis.ui.chat

import com.jarvis.data.approvals.ApprovalRepository
import com.jarvis.data.approvals.ApprovalStatus
import com.jarvis.data.chat.ChatRepository
import com.jarvis.data.chat.SessionStore
import com.jarvis.data.net.ApprovalApi
import com.jarvis.data.net.ApprovalDecisionDto
import com.jarvis.data.net.ApprovalDto
import com.jarvis.data.net.ApprovalsResponse
import com.jarvis.data.net.ChatRequest
import com.jarvis.data.net.ChatResponse
import com.jarvis.data.net.HistoryMessage
import com.jarvis.data.net.HistoryResponse
import com.jarvis.data.net.JarvisApi
import com.jarvis.data.net.ReasonsResponse
import com.jarvis.data.net.RejectReasonDto
import com.jarvis.data.net.RejectRequest
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
import java.io.IOException

/**
 * The approval half of the chat state machine (spec §4.3, §7, §10).
 *
 * Three invariants are load-bearing here and each has its own test:
 *
 * 1. **The queue is independent of push.** A launch syncs `GET /api/approvals`, so a
 *    dropped FCM notification cannot make a pending red action invisible (§4.3).
 * 2. **The badge is never read from the transcript.** The card's status comes from the
 *    approval document, because that document is the single source of truth (§7). A
 *    transcript row is a permanent record of "a card was created", not of its outcome.
 * 3. **A decision is never applied optimistically** (3d-3 lesson). The card moves to the
 *    status the SERVER returned — which may be `expired` when the button said approve.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatApprovalsTest {

    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    private class FakeSessionStore : SessionStore {
        override suspend fun sessionId() = "s-1"
        override suspend fun startNew() = "s-1"
        override suspend fun switchTo(sessionId: String) {}
    }

    private class FakeApi(var history: HistoryResponse = HistoryResponse(emptyList())) : JarvisApi {
        override suspend fun chat(req: ChatRequest) = ChatResponse("ok")
        override suspend fun history(sessionId: String) = history
    }

    private class FakeApprovalApi(
        var queue: List<ApprovalDto> = emptyList(),
        var singles: Map<String, ApprovalDto> = emptyMap(),
        var decision: ApprovalDecisionDto = ApprovalDecisionDto("approved", "oldu", false),
        var presetReasons: List<RejectReasonDto> = emptyList(),
        var listFailure: Throwable? = null,
        var decideFailure: Throwable? = null,
    ) : ApprovalApi {
        var listCalls = 0
        var approveCalls = 0
        var rejectCalls = 0
        var lastRejectReason: String? = null
        var fetched = mutableListOf<String>()

        override suspend fun list(): ApprovalsResponse {
            listCalls++
            listFailure?.let { throw it }
            return ApprovalsResponse(queue)
        }
        override suspend fun get(id: String): ApprovalDto {
            fetched += id
            return singles[id] ?: throw IllegalStateException("bilinmeyen onay: $id")
        }
        override suspend fun approve(id: String): ApprovalDecisionDto {
            approveCalls++
            decideFailure?.let { throw it }
            return decision
        }
        override suspend fun reject(id: String, request: RejectRequest): ApprovalDecisionDto {
            rejectCalls++
            lastRejectReason = request.reason
            decideFailure?.let { throw it }
            return decision
        }

        override suspend fun reasons(): ReasonsResponse = ReasonsResponse(presetReasons)
    }

    private fun dto(id: String, status: String = "pending", title: String = "'cancel_reminder' çalıştırılsın mı?") =
        ApprovalDto(
            id = id,
            kind = "tool_call",
            title = title,
            detail = "Kırmızı bölge eylemi: cancel_reminder(reminder_id=r7).",
            tool_name = "cancel_reminder",
            status = status,
            created_at = "2026-08-03T10:0$id:00+00:00",
        )

    private fun cardRow(approvalId: String) = HistoryMessage(
        role = "model",
        text = "🔔 Onay bekliyor — 'cancel_reminder' çalıştırılsın mı?",
        ts = "t1",
        kind = "approval",
        meta = mapOf("approval_id" to approvalId),
    )

    private fun vm(api: FakeApi = FakeApi(), approvals: FakeApprovalApi = FakeApprovalApi()) =
        ChatViewModel(
            repo = ChatRepository(api, FakeSessionStore()),
            approvals = ApprovalRepository(approvals),
        )

    // --- 0. voice overlay closing re-syncs the queue -------------------------------

    /**
     * 4 Ağu 01:49 vakası: ses görüşmesi İÇİNDE doğan onay kartı, overlay kapanınca
     * görünmüyordu — overlay alttaki sohbete hiçbir yaşam-döngüsü olayı vermez ve
     * kuyruk yalnız açılışta/gönderimde senkronlanıyordu. Görüşme bitişi artık bir
     * senkron tetiğidir.
     */
    @Test
    fun voiceCallEnding_syncsTheQueue_soAVoiceBornApprovalAppears() = runTest(dispatcher) {
        val approvals = FakeApprovalApi()
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        approvals.queue = listOf(dto("7"))
        vm.onVoiceCallEnded()
        advanceUntilIdle()

        assertEquals(2, approvals.listCalls)
        assertEquals(listOf("7"), vm.state.value.pinnedApprovals.map { it.id })
    }

    /**
     * Overlay bayrağı açılışta da bir kez "kapalı" olarak gözlemlenir (LaunchedEffect
     * ilk kompozisyonda koşar) — oturum açılmadan ağa çıkmak, girişten önce
     * "Onaylar şu an okunamıyor" hatası basardı. Girişsiz çağrı YOK sayılır.
     */
    @Test
    fun voiceCallEnding_beforeSignIn_staysOffTheNetwork() = runTest(dispatcher) {
        val approvals = FakeApprovalApi()
        val vm = vm(approvals = approvals)

        vm.onVoiceCallEnded()
        advanceUntilIdle()

        assertEquals(0, approvals.listCalls)
        assertNull(vm.state.value.error)
    }

    // --- 1. queue sync ------------------------------------------------------------

    /**
     * Spec §4.3, the reason the queue endpoint exists at all: the push may never arrive.
     * The transcript here is EMPTY and the pending approval must still show up.
     */
    @Test
    fun signingIn_syncsTheQueue_soAMissedPushCannotHideAPendingApproval() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)

        vm.onSignedIn()
        advanceUntilIdle()

        assertEquals(1, approvals.listCalls)
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
        // Nothing in the transcript refers to it, so it is pinned below the thread.
        assertEquals(listOf("1"), vm.state.value.pinnedApprovals.map { it.id })
    }

    /**
     * The card is IN the transcript, so it must not also be pinned — Kadir would see the
     * same red action twice and could tap either copy.
     */
    @Test
    fun anApprovalDrawnInTheThread_isNotAlsoPinnedBelowIt() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(FakeApi(HistoryResponse(listOf(cardRow("1")))), approvals)

        vm.onSignedIn()
        advanceUntilIdle()

        assertTrue(vm.state.value.messages.single().isApprovalCard)
        assertNotNull(vm.state.value.approvals["1"])
        assertTrue("transcript'teki kart ayrıca sabitlenmemeli", vm.state.value.pinnedApprovals.isEmpty())
    }

    /**
     * Spec §7. The transcript row still says "🔔 Onay bekliyor" — it always will, it is an
     * append-only record — but the approval was decided since. The badge must come from
     * the document, so the card reads `GET /api/approvals/{id}` for a row the queue no
     * longer contains.
     */
    @Test
    fun aDecidedTranscriptCard_readsItsStatusFromTheDocument_notFromTheRowText() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = emptyList(),                       // already decided: not pending
            singles = mapOf("1" to dto("1", status = "approved")),
        )
        val vm = vm(FakeApi(HistoryResponse(listOf(cardRow("1")))), approvals)

        vm.onSignedIn()
        advanceUntilIdle()

        assertEquals(listOf("1"), approvals.fetched)
        assertEquals(ApprovalStatus.APPROVED, vm.state.value.approvals["1"]!!.status)
        assertFalse(vm.state.value.canDecide("1"))
    }

    /** A row already covered by the queue read needs no second request. */
    @Test
    fun aPendingTranscriptCard_isNotFetchedTwice() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(FakeApi(HistoryResponse(listOf(cardRow("1")))), approvals)

        vm.onSignedIn()
        advanceUntilIdle()

        assertTrue("kuyrukta olan kart tekrar okunmamalı", approvals.fetched.isEmpty())
    }

    /**
     * An empty list means "no approvals"; a FAILED read means "we could not look". Wiping
     * the cards on failure would quietly retract a red action that is still waiting.
     */
    @Test
    fun aFailedQueueRead_keepsTheCardsOnScreen_andSaysSoInTurkish() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()
        assertEquals(1, vm.state.value.approvals.size)

        approvals.listFailure = IOException("offline")
        vm.refreshHistory()
        advanceUntilIdle()

        assertEquals("kart korunmalı", 1, vm.state.value.approvals.size)
        assertNotNull(vm.state.value.error)
        assertTrue(vm.state.value.error!!.contains("Bağlantı kurulamadı"))
    }

    // --- 2. decisions are the server's ---------------------------------------------

    /**
     * The 3d-3 lesson made visible: the button said APPROVE and the server said EXPIRED
     * (the TTL ran out between the card being drawn and the tap, spec §4.1). An optimistic
     * card would show "Onaylandı" for an action that never ran.
     */
    @Test
    fun approving_takesTheServersStatus_notTheButtons() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("1")),
            decision = ApprovalDecisionDto(
                "expired", "Onay süresi doldu; eylem çalıştırılmadı.", already = true,
            ),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("1")
        advanceUntilIdle()

        assertEquals(ApprovalStatus.EXPIRED, vm.state.value.approvals["1"]!!.status)
        assertEquals(
            "Onay süresi doldu; eylem çalıştırılmadı.",
            vm.state.value.approvals["1"]!!.outcome,
        )
    }

    @Test
    fun rejecting_takesTheServersStatus() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("1")),
            decision = ApprovalDecisionDto("rejected", null, already = false),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.rejectApproval("1", "yanlış kişi")
        advanceUntilIdle()

        assertEquals(1, approvals.rejectCalls)
        assertEquals("yanlış kişi", approvals.lastRejectReason)
        assertEquals(0, approvals.approveCalls)
        assertEquals(ApprovalStatus.REJECTED, vm.state.value.approvals["1"]!!.status)
    }

    @Test
    fun beginReject_doesNotCallRejectApi_andPopulatesReasons() = runTest(dispatcher) {
        val preset = listOf(RejectReasonDto("r1", "Yanlış kişi", "Yanlış kişi seçildi."))
        val approvals = FakeApprovalApi(queue = listOf(dto("1")), presetReasons = preset)
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.beginReject("1")
        advanceUntilIdle()

        assertEquals(0, approvals.rejectCalls)
        assertEquals("1", vm.state.value.rejectingApprovalId)
        assertEquals(1, vm.state.value.rejectionReasons.size)
        assertEquals("Yanlış kişi", vm.state.value.rejectionReasons[0].title)
    }

    @Test
    fun confirmReject_sendsReasonToApi_andClearsRejectingId() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("1")),
            decision = ApprovalDecisionDto("rejected", null, already = false),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.beginReject("1")
        assertEquals("1", vm.state.value.rejectingApprovalId)

        vm.confirmReject("yanlış kişi")
        advanceUntilIdle()

        assertEquals(1, approvals.rejectCalls)
        assertEquals("yanlış kişi", approvals.lastRejectReason)
        assertNull(vm.state.value.rejectingApprovalId)
        assertEquals(ApprovalStatus.REJECTED, vm.state.value.approvals["1"]!!.status)
    }

    @Test
    fun confirmReject_blankReason_refusedLocally() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.beginReject("1")
        vm.confirmReject("   ")
        advanceUntilIdle()

        assertEquals(0, approvals.rejectCalls)
        assertNotNull(vm.state.value.error)
        assertNull(vm.state.value.rejectingApprovalId)
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
    }

    @Test
    fun approveApproval_clearsRejectingIdIfMatching() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.beginReject("1")
        assertEquals("1", vm.state.value.rejectingApprovalId)

        vm.approveApproval("1")
        advanceUntilIdle()

        assertNull(vm.state.value.rejectingApprovalId)
        assertEquals(1, approvals.approveCalls)
    }

    /**
     * The pin against optimism, asserted in the window where an optimistic update would
     * be visible: after the tap, before the response.
     */
    @Test
    fun whileTheDecisionIsInFlight_theCardIsStillPending() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("1")

        // Synchronous, i.e. the frame right after the tap.
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
        assertTrue(vm.state.value.isDeciding("1"))
    }

    /**
     * The 10296e1 lesson: a disabled action must LOOK disabled. [ChatUiState.canDecide] is
     * the ONE flag the card reads for both `enabled` and its appearance, so a build where
     * the buttons look live while a decision is in flight cannot exist — the two cannot
     * drift apart when there is only one of them.
     */
    @Test
    fun whileTheDecisionIsInFlight_theButtonsAreNotDecidable() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()
        assertTrue(vm.state.value.canDecide("1"))

        vm.approveApproval("1")
        assertFalse("karar uçarken düğmeler kapalı olmalı", vm.state.value.canDecide("1"))

        advanceUntilIdle()
        assertFalse("karar verildi, düğmeler geri gelmemeli", vm.state.value.canDecide("1"))
        assertFalse(vm.state.value.isDeciding("1"))
    }

    /**
     * A double tap must not send a second decision. The server is idempotent (claim
     * document, §4.2) so this is not a correctness hole, but a second request is a second
     * chance to show the user a confusing "already" verdict for their own tap.
     */
    @Test
    fun tappingTwiceWhileDeciding_sendsOneDecision() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(queue = listOf(dto("1")))
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("1")
        vm.approveApproval("1")
        vm.rejectApproval("1")
        advanceUntilIdle()

        assertEquals(1, approvals.approveCalls)
        assertEquals(0, approvals.rejectCalls)
    }

    /** An already-decided card has no buttons, so it cannot be decided again from here. */
    @Test
    fun aDecidedCard_cannotBeDecidedAgain() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = emptyList(),
            singles = mapOf("1" to dto("1", status = "rejected")),
        )
        val vm = vm(FakeApi(HistoryResponse(listOf(cardRow("1")))), approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("1")
        advanceUntilIdle()

        assertEquals(0, approvals.approveCalls)
        assertEquals(ApprovalStatus.REJECTED, vm.state.value.approvals["1"]!!.status)
    }

    // --- 3. failures are Turkish and change nothing ---------------------------------

    /**
     * A failed decision must leave the card exactly where it was. Anything else would be
     * an optimistic update with extra steps: the user would see "Onaylandı" for a request
     * the server never accepted.
     */
    @Test
    fun aFailedDecision_leavesTheCardPending_andShowsATurkishError() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("1")),
            decideFailure = RuntimeException("boom"),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("1")
        advanceUntilIdle()

        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
        assertTrue("tekrar denenebilmeli", vm.state.value.canDecide("1"))
        assertFalse(vm.state.value.isDeciding("1"))
        assertTrue(vm.state.value.error!!.contains("Onaylanamadı"))
    }

    /** Class-based humanising, the existing [com.jarvis.data.net.userMessage] pattern. */
    @Test
    fun aNetworkFailure_readsAsTheNetworkMessage_notAsAServerRefusal() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("1")),
            decideFailure = IOException("no route to host"),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.rejectApproval("1", "yanlış kişi")
        advanceUntilIdle()

        assertEquals("Bağlantı kurulamadı. İnterneti kontrol edip tekrar dene.", vm.state.value.error)
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
    }

    // --- 4. arriving from a notification --------------------------------------------

    /**
     * `fcm.send_approval` puts `data.approval_id` on the push; tapping it must land on
     * THAT card (spec §10). The approval may not be in the queue at all by then — decided
     * from another device, or expired — so the id is fetched directly.
     */
    @Test
    fun openingFromANotification_focusesThatCard_andFetchesIt() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(singles = mapOf("1" to dto("1")))
        val vm = vm(approvals = approvals)

        vm.focusApproval("1")
        advanceUntilIdle()

        assertEquals("1", vm.state.value.focusedApprovalId)
        assertEquals(listOf("1"), approvals.fetched)
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["1"]!!.status)
    }

    /**
     * The push may point at an approval that was decided from another device, or that
     * expired while the phone was locked — and its transcript row may belong to a
     * conversation Kadir is not reading. "Tapping the push shows that card" (spec §10) has
     * to survive that: a card reading "Süresi doldu" is an answer, a blank screen is not.
     */
    @Test
    fun openingFromANotification_showsAnAlreadyDecidedCard_ratherThanNothing() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            singles = mapOf("1" to dto("1", status = "expired")),
        )
        val vm = vm(approvals = approvals)

        vm.focusApproval("1")
        advanceUntilIdle()

        assertEquals(listOf("1"), vm.state.value.pinnedApprovals.map { it.id })
        assertEquals(ApprovalStatus.EXPIRED, vm.state.value.pinnedApprovals[0].status)
        assertFalse(vm.state.value.canDecide("1"))
    }

    /** A launch with no notification extra must not go looking for an approval. */
    @Test
    fun openingWithoutANotification_focusesNothing() = runTest(dispatcher) {
        val approvals = FakeApprovalApi()
        val vm = vm(approvals = approvals)

        vm.focusApproval(null)
        vm.focusApproval("  ")
        advanceUntilIdle()

        assertNull(vm.state.value.focusedApprovalId)
        assertTrue(approvals.fetched.isEmpty())
    }

    // --- 5. the approvals repository is optional --------------------------------------

    /**
     * Existing tests build a ChatViewModel with no approval repository at all. Nothing may
     * crash or hang there — approvals are a feature of the screen, not a precondition of it.
     */
    @Test
    fun withNoApprovalRepository_theChatStillWorks() = runTest(dispatcher) {
        val vm = ChatViewModel(ChatRepository(FakeApi(), FakeSessionStore()))

        vm.onSignedIn()
        vm.approveApproval("1")
        vm.rejectApproval("1")
        vm.focusApproval("1")
        advanceUntilIdle()

        assertTrue(vm.state.value.approvals.isEmpty())
        assertNull(vm.state.value.error)
    }

    // --- 5. a turn that RAISES an approval (review C1) -----------------------------

    /**
     * The flow that creates the card must also show it. A red-zone tool call becomes a
     * pending approval inside the turn (spec §5): the server writes the card into the
     * transcript and pushes it, but [ChatViewModel.send] appends the reply LOCALLY and
     * never re-reads history. Without a sync here Kadir reads "onay kartı gönderdim" and
     * sees no card at all — and there is no pull-to-refresh in this screen.
     */
    @Test
    fun aTurnThatRaisesAnApproval_showsTheCardWithoutARestart() = runTest(dispatcher) {
        val approvals = FakeApprovalApi()
        val vm = vm(approvals = approvals)
        advanceUntilIdle()
        val listCallsBeforeTurn = approvals.listCalls

        // The turn happens, and the server raises the card while serving it.
        approvals.queue = listOf(dto("7"))
        vm.onInputChange("su iç hatırlatmasını iptal et")
        vm.send()
        advanceUntilIdle()

        assertEquals(listCallsBeforeTurn + 1, approvals.listCalls)
        assertEquals(ApprovalStatus.PENDING, vm.state.value.approvals["7"]?.status)
        assertTrue(vm.state.value.pinnedApprovals.any { it.id == "7" })
    }

    /** A stumble while syncing must not delete a reply that was actually delivered. */
    @Test
    fun aFailedApprovalSyncAfterASend_keepsTheReply() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(listFailure = IOException("ağ yok"))
        val vm = vm(approvals = approvals)
        advanceUntilIdle()

        vm.onInputChange("merhaba")
        vm.send()
        advanceUntilIdle()

        val texts = vm.state.value.messages.map { it.text }
        assertTrue("kullanıcı balonu duruyor", texts.contains("merhaba"))
        assertTrue("cevap duruyor", texts.contains("ok"))
        assertEquals("", vm.state.value.input)
    }

    /**
     * The claim race: the loser re-reads the approval document, and the winner may not
     * have written its status projection yet, so the answer can be `already` + still
     * "pending". Adopted as-is the card would look decidable although the action had
     * already run — one re-read settles it.
     */
    @Test
    fun anAlreadyDecidedRaceLoser_refetchesInsteadOfStayingPending() = runTest(dispatcher) {
        val approvals = FakeApprovalApi(
            queue = listOf(dto("9")),
            decision = ApprovalDecisionDto("pending", null, already = true),
            singles = mapOf("9" to dto("9", status = "approved")),
        )
        val vm = vm(approvals = approvals)
        vm.onSignedIn()
        advanceUntilIdle()

        vm.approveApproval("9")
        advanceUntilIdle()

        assertTrue("onay yeniden okundu", approvals.fetched.contains("9"))
        assertEquals(ApprovalStatus.APPROVED, vm.state.value.approvals["9"]?.status)
    }
}
