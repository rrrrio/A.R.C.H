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

  it('shows the rebuilding state even if an old enabled setting remains present', () => {
    expect(stateFromAvatarStatus({
      enabled: false,
      state: 'rebuilding',
      model: 'wav2lip',
      source_video: 'idle.mp4',
      detail: 'Video avatar is being rebuilt. Ordinary voice remains available.',
    })).toBe('rebuilding')
  })
})

describe('AvatarVideoClient', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('negotiates recv-only audio and video without transferring playback ownership early', async () => {
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
    const ownership = vi.fn()
    const client = new AvatarVideoClient('csrf-token', video, () => undefined, () => undefined, ownership)

    await client.connect()
    await client.close()

    expect(transceivers).toEqual(['audio', 'video'])
    expect(video.muted).toBe(true)
    expect(ownership).not.toHaveBeenCalledWith(true)
    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/avatar/offer', expect.objectContaining({ method: 'POST' }))
    expect(fetchMock).toHaveBeenLastCalledWith('/api/avatar/session', {
      method: 'DELETE',
      headers: { 'X-CSRF-Token': 'csrf-token' },
    })
  })
})
