import { afterEach, describe, expect, it, vi } from 'vitest'

import { isOfferResponse, isVoiceEvent, VoiceClient } from './voice'

describe('isOfferResponse', () => {
  it('accepts a complete server answer', () => {
    expect(isOfferResponse({ sdp: 'v=0', type: 'answer', pc_id: 'connection-1' })).toBe(true)
  })

  it('rejects incomplete or malformed answers', () => {
    expect(isOfferResponse({ sdp: 'v=0', type: 'offer', pc_id: 'connection-1' })).toBe(false)
    expect(isOfferResponse({ sdp: 'v=0', type: 'answer' })).toBe(false)
  })
})

describe('isVoiceEvent', () => {
  it('accepts the bounded state and transcript protocol', () => {
    expect(isVoiceEvent({ type: 'voice_state', state: 'speaking' })).toBe(true)
    expect(isVoiceEvent({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'delta', text: '你好' })).toBe(true)
  })

  it('rejects unknown activity and transcript phases', () => {
    expect(isVoiceEvent({ type: 'voice_state', state: 'running' })).toBe(false)
    expect(isVoiceEvent({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'chunk', text: '你好' })).toBe(false)
  })
})

describe('VoiceClient', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('releases the server session after an offer request fails before its peer id is received', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack
    const stream = { getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream

    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } })
    vi.stubGlobal(
      'Audio',
      class {
        autoplay = false
        srcObject: MediaProvider | null = null

        pause() {}

        play() {
          return Promise.resolve()
        }
      },
    )
    vi.stubGlobal(
      'RTCPeerConnection',
      class {
        ontrack: ((event: RTCTrackEvent) => void) | null = null
        onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null

        addTrack() {}

        async createOffer() {
          return { type: 'offer' as const, sdp: 'v=0' }
        }

        async setLocalDescription() {}

        async setRemoteDescription() {}

        close() {}

        createDataChannel() {
          return { close() {}, onmessage: null } as unknown as RTCDataChannel
        }
      },
    )

    const client = new VoiceClient('csrf-token', () => undefined)
    await expect(client.connect()).rejects.toThrow('network unavailable')
    await client.close()

    expect(track.stop).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenLastCalledWith('/api/voice/session', {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': 'csrf-token' },
    })
  })
})
