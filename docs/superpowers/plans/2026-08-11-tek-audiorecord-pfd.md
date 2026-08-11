# Tek-AudioRecord → PFD Beslemesi Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `AudioRecord` feeds BOTH the server speaker-ID stream and the platform speech recognizer (via `RecognizerIntent.EXTRA_AUDIO_SOURCE` + `ParcelFileDescriptor`), eliminating the second capture client — the recognizer's private microphone — which is the prime suspect for the first-words loss and the reason the STT leg of the AEC chain is unmeasurable.

**Architecture:** `AndroidMicSource` gains a thread-safe PCM tap invoked on every frame it reads (BEFORE `VoiceSession`'s echo gate — the recognizer must keep hearing everything, exactly as its own mic does today, so barge-in/echo semantics do not change). `AndroidSpeechToText` gains a PFD mode: per `listen()` cycle it creates a fresh pipe, registers a bounded-queue writer on the tap, and hands the read end to `startListening`. A pure `PfdFeedPolicy` class (JVM-tested) owns the drop accounting and the automatic fallback decision: if PFD cycles produce nothing on a device whose recognizer ignores the pipe, the session flips back to the legacy own-mic mode and says so loudly. `VoiceSession` is **untouched**.

**Tech Stack:** Kotlin, `AudioRecord`, `SpeechRecognizer`, `ParcelFileDescriptor.createPipe()`, JUnit (JVM) + one instrumented emulator test.

## Evidence this plan rests on (do not re-litigate)

- **Task 7 probe (2026-08-11, commit `2ca37e2`):** `VERDICT=PFD_CONSUMED_TRANSCRIPT bytesWritten=192000 writerDone=true` — the tr-TR recognizer fully drained a piped source and transcribed it correctly (emulator, network recognizer path). Mechanical discriminator: fixture (192 KB) > pipe buffer (~64 KB), so writer completion proves consumption.
- **Probe trap 1:** closing our copy of `readFd` right after `startListening()` returns races the recognizer's async Binder handoff → spurious `ERROR:5`. Close it only at cycle end.
- **Probe trap 2:** `EXTRA_AUDIO_SOURCE` needs the sibling extras (`_SAMPLING_RATE` 16000, `_CHANNEL_COUNT` 1, `_ENCODING` PCM_16BIT).
- **Honest limit:** Pixel's on-device Soda path is UNVERIFIED (emulator lacked the tr-TR pack; the network recognizer answered). Hence the runtime fallback, and the final on-device verdict belongs to the Kadir device round.

## Global Constraints

- Turkish for user-visible strings (none expected — this plan is plumbing); English for code, identifiers, comments, commit messages.
- No mock/hardcoded stand-in data in production paths.
- DATA-level logging for the new mechanics: pipe cycle start/end, bytes written, drops, fallback flips — one-line greppable, `tl ev=` style (no `t=`/`gen=`; `AndroidSpeechToText`'s documented convention relies on logcat timestamps).
- `VoiceSession.kt` must not change (behavioral or otherwise). Echo-guard semantics for SERVER frames unchanged; the recognizer feed is deliberately ungated (parity with its own mic today).
- Suites: `cd android && JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:testDebugUnitTest :app:assembleDebug :app:compileDebugAndroidTestKotlin` — all three, every task. Check `free -g` ≥ 4 first.
- Instrumented runs: emulator only, `ANDROID_SERIAL=emulator-5554`, runner-arg class filter (`-Pandroid.testInstrumentationRunnerArguments.class=...`; `--tests` is invalid on this AGP). Never run connected tests unpinned.
- Git: stage by explicit path only; never `-A`/`-am`.
- Branch: `feat/antispoof-cm`.

## File Structure

- Create: `android/app/src/main/java/com/jarvis/data/voice/session/PfdFeedPolicy.kt` — pure decision/bookkeeping (JVM-testable).
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AudioIo.kt` — add the `PcmTapSource` interface.
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidMicSource.kt` — implement the tap.
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidSpeechToText.kt` — PFD mode.
- Modify: `android/app/src/main/java/com/jarvis/JarvisApp.kt` — wiring only.
- Test: `android/app/src/test/java/com/jarvis/data/voice/session/PfdFeedPolicyTest.kt` (create), `android/app/src/androidTest/java/com/jarvis/data/voice/session/PfdSpeechToTextTest.kt` (create).

---

### Task 1: `PfdFeedPolicy` — pure fallback + drop bookkeeping

**Files:**
- Create: `android/app/src/main/java/com/jarvis/data/voice/session/PfdFeedPolicy.kt`
- Test: `android/app/src/test/java/com/jarvis/data/voice/session/PfdFeedPolicyTest.kt`

**Interfaces:**
- Consumes: nothing.
- Produces (Task 3 relies on these exact signatures):
  - `class PfdFeedPolicy { fun onCycleEnd(hadPartial: Boolean, hadResult: Boolean, bytesWritten: Long): Unit; fun shouldUsePfd(): Boolean; fun cycleSummary(): String }`
  - Semantics: starts in PFD mode. A cycle that produced NO partial and NO result while fewer than `MIN_CONSUMED_BYTES` (32_000 = 1 s of 16 kHz PCM16) were written counts as a failure; `FALLBACK_AFTER` (2) CONSECUTIVE failures flips `shouldUsePfd()` to false permanently for this instance (one instance per call). Any cycle with a partial, a result, or ≥ `MIN_CONSUMED_BYTES` written resets the streak. `cycleSummary()` returns the one-line DATA string for logging.

- [ ] **Step 1: Write the failing tests**

```kotlin
package com.jarvis.data.voice.session

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PfdFeedPolicyTest {
    @Test fun startsInPfdMode() {
        assertTrue(PfdFeedPolicy().shouldUsePfd())
    }

    @Test fun twoConsecutiveDeadCyclesFlipToLegacy() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        assertTrue(p.shouldUsePfd()) // one dead cycle is not proof
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 100)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun aLiveCycleResetsTheStreak() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        p.onCycleEnd(hadPartial = true, hadResult = false, bytesWritten = 500)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun consumedBytesAloneCountAsAlive() {
        // The recognizer drained ≥1s of audio but endpointed on silence with no
        // text: the PIPE works, there was just nothing to hear. Not a failure.
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 32_000)
        p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 32_000)
        assertTrue(p.shouldUsePfd())
    }

    @Test fun theFlipIsPermanentForThisInstance() {
        val p = PfdFeedPolicy()
        repeat(2) { p.onCycleEnd(hadPartial = false, hadResult = false, bytesWritten = 0) }
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 90_000)
        assertFalse(p.shouldUsePfd())
    }

    @Test fun summaryCarriesTheDataFields() {
        val p = PfdFeedPolicy()
        p.onCycleEnd(hadPartial = true, hadResult = true, bytesWritten = 64_000)
        val s = p.cycleSummary()
        assertTrue(s.contains("bytes=64000"))
        assertTrue(s.contains("pfd=true"))
    }
}
```

- [ ] **Step 2: Run to verify failure** — `./gradlew :app:testDebugUnitTest --tests '*PfdFeedPolicyTest*'` → FAIL (class missing).
- [ ] **Step 3: Implement** — a small class holding `failStreak: Int`, `usePfd: Boolean`, `lastSummary: String`; constants `MIN_CONSUMED_BYTES = 32_000L`, `FALLBACK_AFTER = 2` in a companion with KDoc explaining each (why 1 s: a recognizer that reads at all drains far more than 1 s while endpointing; why 2: one dead cycle can be a transient service race — the probe saw RECOGNIZER_BUSY classes — two consecutive is a pattern).
- [ ] **Step 4: Run the test class, then the full JVM suite.**
- [ ] **Step 5: Commit** — `feat(voice): PfdFeedPolicy — fallback decision for the piped recognizer feed`

---

### Task 2: The PCM tap on `AndroidMicSource`

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AudioIo.kt` (add interface at file end)
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidMicSource.kt`

**Interfaces:**
- Produces (Task 3 consumes):

```kotlin
/**
 * A source whose PCM frames can be tapped by ONE additional consumer without
 * disturbing the primary read path. The tap sees every frame the primary
 * consumer reads, BEFORE any gating the session applies -- parity with a
 * recognizer that owns its own microphone. JVM-pure on purpose.
 */
interface PcmTapSource {
    /** Installs (or clears, with null) the single tap. Thread-safe. */
    fun setTap(tap: ((ByteArray) -> Unit)?)
}
```

- `AndroidMicSource` declares `class AndroidMicSource(private val audioManager: AudioManager) : MicSource, PcmTapSource`, holds `@Volatile private var tap: ((ByteArray) -> Unit)? = null`, and in `readFrame()` — after the existing `if (read > 0) buffer.copyOf(read) else null` produces a non-null frame — invokes `tap?.invoke(frame)` BEFORE returning it. The tap runs on the same IO context as the read; Task 3's tap body is a non-blocking queue offer, and the KDoc on `setTap` must state that contract ("the tap must never block: it runs on the capture read path").

- [ ] **Step 1: Implement** (no JVM test can construct `AndroidMicSource` — documented limitation in the file header; the instrumented test in Task 4 covers the tap end-to-end). Keep the change minimal: interface + field + one invoke + KDoc.
- [ ] **Step 2: Run** `:app:testDebugUnitTest :app:assembleDebug :app:compileDebugAndroidTestKotlin` — all green (no behavior change for existing consumers; `VoiceSessionTest`'s fake `MicSource` does not implement `PcmTapSource` and must not need to).
- [ ] **Step 3: Commit** — `feat(voice): AndroidMicSource exposes a non-blocking PCM tap`

---

### Task 3: PFD mode in `AndroidSpeechToText`

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/data/voice/session/AndroidSpeechToText.kt`
- Modify: `android/app/src/main/java/com/jarvis/JarvisApp.kt` (constructor wiring only)

**Interfaces:**
- Consumes: `PcmTapSource.setTap` (Task 2), `PfdFeedPolicy` (Task 1).
- Produces: unchanged `SpeechToText` interface — `VoiceSession` must not know any of this exists.

**Design (implementer: read the current file first; line numbers shift):**

1. Constructor becomes `AndroidSpeechToText(private val context: Context, private val tapSource: PcmTapSource? = null)`. `JarvisApp` passes the `AndroidMicSource` instance it already constructs (it implements `PcmTapSource` after Task 2). A null `tapSource` = legacy mode forever (also the safety hatch).
2. One `PfdFeedPolicy` instance per `start()` call (a call = one policy lifetime, matching "permanent for this instance" semantics).
3. In `listen()`, when `tapSource != null && policy.shouldUsePfd()`:
   - Create `ParcelFileDescriptor.createPipe()`; keep both fds in the cycle state.
   - Start a writer: `ArrayBlockingQueue<ByteArray>(32)` + a daemon thread draining it into `ParcelFileDescriptor.AutoCloseOutputStream(writeFd)`. The TAP body is `queue.offer(frame)` — never blocking; count rejected offers (`drops`).
   - Install the tap (`tapSource.setTap { ... }`), build the intent as today PLUS:
     ```kotlin
     putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readFd)
     putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, 16000)
     putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
     putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
     ```
   - Do NOT close our `readFd` copy after `startListening` (probe trap 1) — close it in cycle cleanup.
4. Cycle cleanup — runs on EVERY terminal recognizer callback (`onResults`, `onError`) and on `destroy()`: clear the tap, interrupt/stop the writer (poison-pill or flag; bounded join ≤ 500 ms), close write side (via the stream's `.use`/explicit close guarded by try/catch IOException), close our `readFd` copy (guarded), then `policy.onCycleEnd(hadPartial, hadResult, bytesWritten)` and `Log.i(TAG, "tl ev=pfd.cycle " + policy.cycleSummary() + " drops=$drops")`. The NEXT `listen()` builds a fresh pipe. Track `hadPartial`/`hadResult` per cycle in the existing recognition listener (they are cycle-scoped booleans reset in `listen()`).
5. When the policy has flipped (or `tapSource == null`), `listen()` builds the intent WITHOUT the four extras — byte-identical to today's behavior — and logs `tl ev=pfd.fallback` ONCE at flip time.
6. Threading: all cycle state is touched from the main thread (every `SpeechRecognizer` call and callback is already marshalled there — see the file's threading doc); the writer thread and the tap touch ONLY the queue and the atomic byte counter. Keep it that way and document it.

- [ ] **Step 1: Implement per the design.** No new JVM test can drive this class (documented); Task 4's instrumented test is the verification. Self-check list before committing: every terminal path runs cleanup exactly once (guard with a cycle-generation int); `destroy()` mid-cycle cleans up; two rapid `listen()` calls do not leak the first cycle's pipe.
- [ ] **Step 2: Run** all three Gradle gates.
- [ ] **Step 3: Commit** — `feat(voice): feed the recognizer from the session AudioRecord via a piped PFD source`

---

### Task 4: Instrumented proof on the emulator

**Files:**
- Create: `android/app/src/androidTest/java/com/jarvis/data/voice/session/PfdSpeechToTextTest.kt`

**Interfaces:**
- Consumes: `AndroidSpeechToText(context, tapSource)`, `PcmTapSource`.
- Produces: a recorded pass/fail that OUR plumbing (tap → queue → pipe → recognizer) yields a transcript on a real recognizer — one level above Task 7's raw probe.

**Design:** a `FakeTapSource : PcmTapSource` that, once a tap is installed, feeds `tr_probe_16k.pcm` (the committed androidTest asset — read via `InstrumentationRegistry.getInstrumentation().context`, NOT ApplicationProvider) in 1280-byte frames at ~real-time pace (Thread.sleep(40) per frame) from a background thread, then keeps feeding silence (zero-filled frames) so the recognizer endpoints naturally. Construct `AndroidSpeechToText(targetContext, fakeTapSource)`, `start(listener)`, `listen()`, await a result ≤ 20 s (LinkedBlockingQueue, GrantPermissionRule RECORD_AUDIO, availability `assumeTrue` like `RecognizerPipeProbeTest` — copy its conventions). Assert a non-error transcript arrives AND log the `tl ev=pfd.cycle` line. Both outcomes recorded; the assertion may fail honestly here (unlike the probe) because Task 7 already proved the recognizer side — a failure now means OUR plumbing is wrong.

- [ ] **Step 1: Write the test** (conventions from `RecognizerPipeProbeTest.kt` — availability gate, bounded waits, cleanup in finally).
- [ ] **Step 2: Boot the emulator if not running** (`~/Android/Sdk/emulator/emulator -avd jarvis_avd -no-window -no-audio -no-boot-anim -no-snapshot`, background; wait for `sys.boot_completed=1`), then run pinned:
  `ANDROID_SERIAL=emulator-5554 JAVA_HOME=/usr/lib/jvm/java-21-openjdk ./gradlew :app:connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=com.jarvis.data.voice.session.PfdSpeechToTextTest`
- [ ] **Step 3: Read the verdict** from the JUnit XML + `adb -s emulator-5554 logcat -d | grep "pfd.cycle"` (never `logcat -c`).
- [ ] **Step 4: Commit** — `test(voice): instrumented proof that the piped recognizer feed transcribes`

---

### HITL (joins the Kadir device round — NOT a task)

On the Pixel: one normal voice call in PFD mode. Read `tl ev=pfd.cycle` lines (bytes should be tens of KB per utterance, drops ≈ 0) and whether `tl ev=pfd.fallback` fired (that is Soda's answer about PFD on-device). First-words check: compare `stt.first_partial` timing against the pre-PFD baseline from the same morning's round. If fallback fired, the session still works exactly as today — that outcome decides whether we keep PFD-first or gate it on a device allowlist.
