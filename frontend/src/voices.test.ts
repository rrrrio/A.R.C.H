import { describe, expect, it } from 'vitest'

import { createVoiceProfile, handleReferenceFileSelection, readVoiceProfile, restartVoiceSessionForProfileSelection, selectVoiceProfile, transcribeReferenceAudio, validateReferenceAudio } from './voices'

describe('voice profiles', () => {
  it('accepts metadata without a filesystem path or audio payload', () => {
    expect(readVoiceProfile({ id: 'profile-1', name: '我的声音', transcript: '参考文本', sample_rate: 24000, duration_ms: 3200, created_at: '2026-11-10T00:00:00Z' })).toMatchObject({ id: 'profile-1' })
  })

  it('rejects metadata that exposes an incomplete profile', () => {
    expect(readVoiceProfile({ id: 'profile-1', name: '我的声音', transcript: '参考文本' })).toBeNull()
  })

  it('sends an explicit baseline selection', async () => {
    const originalFetch = globalThis.fetch
    const requests: RequestInit[] = []
    globalThis.fetch = async (_, init) => {
      requests.push(init ?? {})
      return new Response(JSON.stringify({ selected_id: null, profiles: [] }), { status: 200 })
    }
    try {
      await selectVoiceProfile('csrf', null)
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(requests[0]?.body).toBe(JSON.stringify({ profile_id: null }))
  })

  it('allows common audio containers and leaves channel conversion to the server', async () => {
    await expect(validateReferenceAudio(new Blob([new Uint8Array([0x49, 0x44, 0x33])], { type: 'audio/mpeg' }))).resolves.toBeUndefined()
  })

  it('rejects an empty or oversized audio reference before upload', async () => {
    await expect(validateReferenceAudio(new Blob())).rejects.toThrow('参考音频文件不能为空')
    await expect(validateReferenceAudio(new Blob([new Uint8Array(5 * 1024 * 1024 + 1)]))).rejects.toThrow('参考音频文件不能超过 5 MiB')
  })

  it('requests an editable transcript draft with the selected audio', async () => {
    const originalFetch = globalThis.fetch
    let request: RequestInit | undefined
    globalThis.fetch = async (_, init) => {
      request = init
      return new Response(JSON.stringify({ transcript: '自动转录文本' }), { status: 200 })
    }
    try {
      await expect(transcribeReferenceAudio('csrf', 'UklGRg==')).resolves.toBe('自动转录文本')
      expect(JSON.parse(String(request?.body))).toEqual({ reference_audio_base64: 'UklGRg==' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('keeps the native file selection visible while starting transcription', () => {
    const file = new File(['audio'], 'reference.mp3', { type: 'audio/mpeg' })
    const input = { files: [file] as unknown as FileList, value: 'C:\\fake\\reference.mp3' }
    let selected: File | null = null

    handleReferenceFileSelection(input, (value) => { selected = value })

    expect(selected).toBe(file)
    expect(input.value).toBe('C:\\fake\\reference.mp3')
  })

  it('restarts an active voice session after selecting a clone profile', async () => {
    const events: string[] = []

    await restartVoiceSessionForProfileSelection(
      'ready',
      async () => { events.push('disconnect') },
      async () => { events.push('connect') },
    )

    expect(events).toEqual(['disconnect', 'connect'])
  })

  it('preserves the server validation detail for an invalid upload', async () => {
    const originalFetch = globalThis.fetch
    let request: RequestInit | undefined
    globalThis.fetch = async (_, init) => {
      request = init
      return new Response(JSON.stringify({ detail: 'Reference WAV must be mono.' }), { status: 422 })
    }
    try {
      await expect(createVoiceProfile('csrf', {
        name: '我的声音',
        transcript: '参考文本',
        referenceAudioBase64: 'UklGRg==',
        ownershipConfirmed: true,
      })).rejects.toThrow('Reference WAV must be mono.')
      expect(JSON.parse(String(request?.body))).toMatchObject({ reference_audio_base64: 'UklGRg==' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})
