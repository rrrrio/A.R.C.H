import { afterEach, describe, expect, it, vi } from 'vitest'

import { isOfferResponse, isVoiceEvent, listAudioInputs, normalizeAudioLevel, pcmEnergy, VoiceClient } from './voice'

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
    expect(isVoiceEvent({ type: 'voice_ready' })).toBe(true)
    expect(isVoiceEvent({ type: 'voice_state', state: 'speaking' })).toBe(true)
    expect(isVoiceEvent({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'delta', text: '你好' })).toBe(true)
    expect(isVoiceEvent({ type: 'latency', utterance_id: 'turn-1', stage: 'llm-first-token', elapsed_ms: 42 })).toBe(true)
  })

  it('rejects unknown activity and transcript phases', () => {
    expect(isVoiceEvent({ type: 'voice_ready', state: 'listening' })).toBe(false)
    expect(isVoiceEvent({ type: 'voice_state', state: 'running' })).toBe(false)
    expect(isVoiceEvent({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'chunk', text: '你好' })).toBe(false)
    expect(isVoiceEvent({ type: 'latency', utterance_id: 'turn-1', stage: 'unknown', elapsed_ms: 42 })).toBe(false)
    expect(isVoiceEvent({ type: 'latency', utterance_id: 'turn-1', stage: 'asr-final', elapsed_ms: -1 })).toBe(false)
  })
})

describe('output energy helpers', () => {
  it('bounds transport data before it reaches the local renderer', () => {
    expect(normalizeAudioLevel(0.4)).toBe(0.4)
    expect(normalizeAudioLevel(2)).toBe(1)
    expect(normalizeAudioLevel(-1)).toBe(0)
    expect(normalizeAudioLevel('0.4')).toBe(0)
    expect(pcmEnergy(new Int16Array())).toBe(0)
    expect(pcmEnergy(new Int16Array([32767, -32768]))).toBe(1)
  })
})

describe('listAudioInputs', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('returns only audio inputs and supplies labels before permission is granted', async () => {
    vi.stubGlobal('navigator', {
      mediaDevices: {
        enumerateDevices: vi.fn().mockResolvedValue([
          { kind: 'videoinput', deviceId: 'camera', label: 'Camera' },
          { kind: 'audioinput', deviceId: 'default', label: '' },
          { kind: 'audioinput', deviceId: 'usb-mic', label: 'USB Mic' },
        ]),
      },
    })

    await expect(listAudioInputs()).resolves.toEqual([
      { deviceId: 'default', label: '麦克风 1' },
      { deviceId: 'usb-mic', label: 'USB Mic' },
    ])
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
        iceGatheringState = 'complete' as RTCIceGatheringState
        localDescription = { type: 'offer' as const, sdp: 'v=0' }

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

  it('mutes interrupted playback and restores it for the next assistant turn', async () => {
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack
    const stream = { getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream
    const receivedEvents: unknown[] = []
    const outputEnergy: number[] = []
    const dataChannel = { close() {}, onmessage: null as ((event: MessageEvent) => void) | null }
    const getUserMedia = vi.fn().mockResolvedValue(stream)

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sdp: 'answer', type: 'answer', pc_id: 'pc-1' }))))
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
    vi.stubGlobal(
      'Audio',
      class {
        autoplay = false
        muted = false
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
        iceGatheringState = 'complete' as RTCIceGatheringState
        localDescription = { type: 'offer' as const, sdp: 'v=0' }

        addTrack() {}

        async createOffer() {
          return { type: 'offer' as const, sdp: 'v=0' }
        }

        async setLocalDescription() {}

        async setRemoteDescription() {}

        close() {}

        createDataChannel() {
          return dataChannel as unknown as RTCDataChannel
        }
      },
    )

    const client = new VoiceClient(
      'csrf-token',
      (event) => receivedEvents.push(event),
      'usb-mic',
      () => undefined,
      (energy) => outputEnergy.push(energy),
    )
    await client.connect()

    expect(getUserMedia).toHaveBeenCalledWith({ audio: { deviceId: { exact: 'usb-mic' } }, video: false })
    dataChannel.onmessage?.({ data: JSON.stringify({ type: 'voice_state', state: 'user-speaking' }) } as MessageEvent)

    expect((client as unknown as { audio: HTMLAudioElement }).audio.muted).toBe(true)
    expect(outputEnergy).toEqual([0])
    dataChannel.onmessage?.({ data: JSON.stringify({ type: 'voice_state', state: 'speaking' }) } as MessageEvent)

    expect((client as unknown as { audio: HTMLAudioElement }).audio.muted).toBe(false)
    expect(receivedEvents).toEqual([
      { type: 'voice_state', state: 'user-speaking' },
      { type: 'voice_state', state: 'speaking' },
    ])
  })

  it('reports an unexpected WebRTC closure once', async () => {
    const track = { stop: vi.fn() } as unknown as MediaStreamTrack
    const stream = { getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream
    const closed = vi.fn()
    type PeerHarness = {
      ontrack: ((event: RTCTrackEvent) => void) | null
      onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null
      onconnectionstatechange: (() => void) | null
      connectionState: RTCPeerConnectionState
    }
    const peers: PeerHarness[] = []

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sdp: 'answer', type: 'answer', pc_id: 'pc-1' }))))
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: vi.fn().mockResolvedValue(stream) } })
    vi.stubGlobal('Audio', class { autoplay = false; srcObject: MediaProvider | null = null; pause() {}; play() { return Promise.resolve() } })
    vi.stubGlobal('RTCPeerConnection', class {
      ontrack: ((event: RTCTrackEvent) => void) | null = null
      onicecandidate: ((event: RTCPeerConnectionIceEvent) => void) | null = null
      onconnectionstatechange: (() => void) | null = null
      connectionState = 'connected' as RTCPeerConnectionState
      iceGatheringState = 'complete' as RTCIceGatheringState
      localDescription = { type: 'offer' as const, sdp: 'v=0' }

      constructor() { peers.push(this) }
      addTrack() {}
      async createOffer() { return { type: 'offer' as const, sdp: 'v=0' } }
      async setLocalDescription() {}
      async setRemoteDescription() {}
      close() {}
      createDataChannel() { return { close() {}, onmessage: null } as unknown as RTCDataChannel }
    })

    const client = new VoiceClient('csrf-token', () => undefined, undefined, closed)
    await client.connect()
    const activePeer = peers[0]
    if (!activePeer) throw new Error('Peer connection was not created.')
    activePeer.connectionState = 'failed'
    activePeer.onconnectionstatechange?.()
    activePeer.onconnectionstatechange?.()

    expect(closed).toHaveBeenCalledOnce()
  })
})
