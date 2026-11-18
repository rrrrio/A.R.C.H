import { describe, expect, it, vi } from 'vitest'

import { notifyAvatarUserSpeaking } from './useVoiceSession'

describe('notifyAvatarUserSpeaking', () => {
  it('forwards the raw VAD interruption event without forwarding other voice events', () => {
    const target = new EventTarget()
    const received = vi.fn()
    target.addEventListener('arch:avatar-user-speaking', received)

    notifyAvatarUserSpeaking({ type: 'voice_state', state: 'speaking' }, target)
    notifyAvatarUserSpeaking({ type: 'voice_state', state: 'user-speaking' }, target)
    notifyAvatarUserSpeaking({ type: 'voice_ready' }, target)

    expect(received).toHaveBeenCalledOnce()
  })
})
