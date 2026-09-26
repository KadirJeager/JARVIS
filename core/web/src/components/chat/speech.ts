// Read-aloud through the browser's own speech synthesis. The voice follows the
// page language (Turkish) when the device has a matching voice; otherwise the
// browser default is used. Nothing is sent to the server.
import type { SpeechSynthesisAdapter } from '@assistant-ui/react'

function plainText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#>*_~|]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export function speechSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

export class DeviceSpeechAdapter implements SpeechSynthesisAdapter {
  speak(text: string): SpeechSynthesisAdapter.Utterance {
    const lang = document.documentElement.lang || navigator.language
    const utterance = new SpeechSynthesisUtterance(plainText(text))
    utterance.lang = lang
    const voice = window.speechSynthesis
      .getVoices()
      .find((candidate) => candidate.lang.toLowerCase().startsWith(lang.slice(0, 2).toLowerCase()))
    if (voice) utterance.voice = voice

    const subscribers = new Set<() => void>()
    const notify = () => subscribers.forEach((callback) => callback())
    const handle: SpeechSynthesisAdapter.Utterance = {
      status: { type: 'starting' },
      cancel: () => {
        window.speechSynthesis.cancel()
        finish('cancelled')
      },
      subscribe: (callback) => {
        subscribers.add(callback)
        return () => subscribers.delete(callback)
      },
    }
    const finish = (reason: 'finished' | 'cancelled' | 'error', error?: unknown) => {
      if (handle.status.type === 'ended') return
      handle.status = { type: 'ended', reason, error }
      notify()
    }
    utterance.onstart = () => {
      handle.status = { type: 'running' }
      notify()
    }
    utterance.onend = () => finish('finished')
    utterance.onerror = (event) => finish(event.error === 'interrupted' ? 'cancelled' : 'error', event.error)
    window.speechSynthesis.cancel()
    window.speechSynthesis.speak(utterance)
    return handle
  }
}
