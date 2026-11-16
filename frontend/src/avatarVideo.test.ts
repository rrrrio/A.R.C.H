import { afterEach, describe, expect, it, vi } from 'vitest'

import { AvatarVideoClient, fetchAvatarStatus, isAvatarStatus, isOfferResponse, stateFromAvatarStatus } from './avatarVideo'

describe('avatar video protocol guards', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('accepts only a complete video answer and status payload', () => {
    expect(isOfferResponse({ sdp: 'v=0', type: 'answer', pc_id: 'avatar-1' })).toBe(true)
    expect(isOfferResponse({ sdp: 'v=0', type: 'offer', pc_id: 'avatar-1' })).toBe(false)
    expect(isAvatarStatus({ enabled: true, state: 'ready', model: 'musetalk-1.5', source_video: 'idle.mp4' })).toBe(true)
    expect(isAvatarStatus({ enabled: true, state: 'ready' })).toBe(false)
  })

  it('fetches authenticated status through the same-origin API', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ enabled: false, state: 'disabled', model: 'musetalk-1.5', source_video: 'idle.mp4' })),
    ))

    await expect(fetchAvatarStatus()).resolves.toMatchObject({ enabled: false, state: 'disabled' })
    expect(fetch).toHaveBeenCalledWith('/api/avatar/status')
  })

  it('keeps video disabled when the enabled renderer has no ready worker', () => {
    expect(stateFromAvatarStatus({
      enabled: true,
      state: 'unavailable',
      model: 'musetalk-1.5',
      source_video: 'idle.mp4',
      detail: 'MuseTalk renderer is not running.',
    })).toBe('unavailable')
  })
})

describe('AvatarVideoClient', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('negotiates a recv-only video track and never creates an audio transceiver', async () => {
    const transceivers: string[] = []
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ sdp: 'v=0', type: 'answer', pc_id: 'avatar-1' })),
    )
    vi.stubGlobal('fetch', fetchMock)
    vi.stubGlobal(
      'RTCPeerConnection',
      class {
        iceGatheringState = 'complete' as RTCIceGatheringState
        connectionState = 'new' as RTCPeerConnectionState
        localDescription = { type: 'offer' as const, sdp: 'v=0' }
        ontrack: ((event: RTCTrackEvent) => void) | null = null
        onconnectionstatechange: (() => void) | null = null

        addTransceiver(kind: string) {
          transceivers.push(kind)
        }

        async createOffer() { return { type: 'offer' as const, sdp: 'v=0' } }
        async setLocalDescription() {}
        async setRemoteDescription() {}
        close() {}
      },
    )
    const video = { muted: false, autoplay: false, playsInline: false, srcObject: null, pause: vi.fn(), play: vi.fn() } as unknown as HTMLVideoElement
    const client = new AvatarVideoClient('csrf-token', video)

    await client.connect()
    await client.close()

    expect(transceivers).toEqual(['video'])
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/avatar/offer', expect.objectContaining({ method: 'POST' }))
    expect(fetchMock).toHaveBeenLastCalledWith('/api/avatar/session', {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': 'csrf-token' },
    })
  })
})
