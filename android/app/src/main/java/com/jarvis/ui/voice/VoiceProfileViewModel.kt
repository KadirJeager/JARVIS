package com.jarvis.ui.voice

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.data.net.userMessage
import com.jarvis.data.voice.VoiceProfileRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * State machine for the speaker-identity management screen (spec §9).
 *
 * No Android types here: the biometric prompt lives at the Activity layer and reports
 * in through [onUnlocked]/[onUnlockFailed], exactly as sign-in does for the chat screen.
 */
class VoiceProfileViewModel(private val repo: VoiceProfileRepository) : ViewModel() {

    private val _state = MutableStateFlow(VoiceProfileUiState())
    val state: StateFlow<VoiceProfileUiState> = _state.asStateFlow()

    /**
     * Re-arm the gate. The host calls this on EVERY entry into the screen, before the
     * prompt is even shown: `route` flips synchronously but `prompt(...)` is async, so
     * without this reset a second visit would render the previous visit's full profile
     * (samples, scores, correction history) underneath the prompt sheet while it is still
     * animating in — the gate protecting nothing after the first unlock of the session.
     */
    fun onGateRequested() = _state.update {
        it.copy(gate = GatePhase.CHECKING, profile = null, summary = null, error = null, deleted = false)
    }

    /** The device lock was satisfied. Only now does anything get fetched. */
    fun onUnlocked() {
        _state.update { it.copy(gate = GatePhase.UNLOCKED, error = null) }
        load()
    }

    fun onUnlockFailed(reason: String?) {
        _state.update {
            it.copy(
                gate = GatePhase.DENIED,
                error = "Kilit açılamadı: ${reason ?: "doğrulama tamamlanmadı"}",
            )
        }
    }

    fun load() {
        _state.update { it.copy(loading = true, error = null) }
        viewModelScope.launch {
            try {
                val profile = repo.load()
                _state.update {
                    it.copy(
                        loading = false,
                        profile = profile,
                        summary = summarize(profile.quality, profile.counts),
                    )
                }
            } catch (e: Exception) {
                _state.update {
                    it.copy(loading = false, error = e.userMessage("Ses kimliği yüklenemedi."))
                }
            }
        }
    }

    fun setLabel(sampleId: String, label: String?) =
        mutate(sampleId, "Etiket güncellenemedi.") { repo.setLabel(sampleId, label) }

    fun deleteSample(sampleId: String) =
        mutate(sampleId, "Örnek silinemedi.") { repo.deleteSample(sampleId) }

    fun confirm(entryId: String) =
        mutate(entryId, "İşlem tamamlanamadı.") { repo.confirm(entryId) }

    fun reject(entryId: String) =
        mutate(entryId, "İşlem tamamlanamadı.") { repo.reject(entryId) }

    fun deleteProfile() {
        if (_state.value.mutatingId != null) return
        _state.update { it.copy(mutatingId = PROFILE_MUTATION_ID, error = null) }
        viewModelScope.launch {
            try {
                repo.deleteProfile()
                _state.update { it.copy(mutatingId = null, deleted = true) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(mutatingId = null, error = e.userMessage("Profil silinemedi."))
                }
            }
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }

    /**
     * One mutation at a time, then a full reload.
     *
     * RELOAD, not a local patch: whether confirm promoted an existing auto sample in
     * place or appended a fresh manual one, whether the cap refused it, whether reject
     * actually removed anything — every one of those is server state (spec §9).
     * Guessing here would put the rules in two places.
     *
     * ONE AT A TIME: the endpoints are idempotent, but two in-flight mutations race
     * each other's reload and the user watches a row blink out. It also stops a double
     * tap from spending a manual-cap slot twice.
     */
    private fun mutate(id: String, fallback: String, block: suspend () -> Unit) {
        if (_state.value.mutatingId != null) return
        _state.update { it.copy(mutatingId = id, error = null) }
        viewModelScope.launch {
            try {
                block()
                _state.update { it.copy(mutatingId = null) }
                load()
            } catch (e: Exception) {
                _state.update { it.copy(mutatingId = null, error = e.userMessage(fallback)) }
            }
        }
    }

    private companion object {
        /** Sentinel so whole-profile deletion shares the single-flight guard. */
        const val PROFILE_MUTATION_ID = "__profile__"
    }
}
