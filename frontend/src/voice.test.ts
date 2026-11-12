import { afterEach, describe, expect, it, vi } from 'vitest'

import { createMutedCaptureSink, decodeBrowserPCM, isOfferResponse, isVoiceEvent, listAudioInputs, normalizeAudioLevel, pcmEnergy, VoiceClient, voiceCaptureConstraints, WebSocketVoiceClient } from './voice'

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
    expect(isVoiceEvent({ type: 'memory_candidates_ready' })).toBe(true)
    expect(isVoiceEvent({ type: 'tool_approvals_ready' })).toBe(true)
    expect(isVoiceEvent({ type: 'voice_state', state: 'speaking' })).toBe(true)
    expect(isVoiceEvent({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'delta', text: '你好' })).toBe(true)
    expect(isVoiceEvent({ type: 'latency', utterance_id: 'turn-1', stage: 'llm-first-token', elapsed_ms: 42 })).toBe(true)
  })

  it('rejects unknown activity and transcript phases', () => {
    expect(isVoiceEvent({ type: 'voice_ready', state: 'listening' })).toBe(false)
    expect(isVoiceEvent({ type: 'memory_candidates_ready', content: 'unexpected' })).toBe(false)
    expect(isVoiceEvent({ type: 'tool_approvals_ready', content: 'unexpected' })).toBe(false)
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

  it('keeps fallback microphone processing silent at the browser destination', () => {
    const connect = vi.fn()
    const sink = { gain: { value: 1 }, connect } as unknown as GainNode
    const destination = {} as AudioDestinationNode

    expect(createMutedCaptureSink({ createGain: () => sink, destination })).toBe(sink)
    expect(sink.gain.value).toBe(0)
    expect(connect).toHaveBeenCalledWith(destination)
  })
})

describe('WebSocket PCM framing', () => {
  it('decodes only a generation-bound PCM packet', () => {
    const payload = new Uint8Array([
      65, 80, 67, 77, 10, 116, 116, 115, 58, 116, 117, 114, 110, 45, 49, 2, 0,
    ]).buffer

    expect(decodeBrowserPCM(payload)).toEqual({
      utteranceId: 'tts:turn-1',
      pcm: new Uint8Array([2, 0]).buffer,
    })
    expect(decodeBrowserPCM(new ArrayBuffer(2))).toBeNull()
  })
})

describe('voice capture constraints', () => {
  it('requests browser echo protection without replacing a chosen microphone', () => {
    expect(voiceCaptureConstraints('usb-mic')).toEqual({
      deviceId: { exact: 'usb-mic' },
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    })
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
    const remoteStream = {} as MediaStream
    const audioInstances: Array<{ autoplay: boolean; muted: boolean; srcObject: MediaProvider | null; pause: ReturnType<typeof vi.fn>; play: ReturnType<typeof vi.fn> }> = []

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ sdp: 'answer', type: 'answer', pc_id: 'pc-1' }))))
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
    vi.stubGlobal(
      'Audio',
      class {
        autoplay = false
        muted = false
        srcObject: MediaProvider | null = null

        pause = vi.fn()

        play = vi.fn().mockResolvedValue(undefined)

        constructor() {
          audioInstances.push(this)
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
    const internalClient = client as unknown as { remoteAudioStream: MediaStream | null }
    internalClient.remoteAudioStream = remoteStream
    if (audioInstances[0]) audioInstances[0].srcObject = remoteStream

    expect(getUserMedia).toHaveBeenCalledWith({
      audio: {
        deviceId: { exact: 'usb-mic' },
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    })
    dataChannel.onmessage?.({ data: JSON.stringify({ type: 'voice_state', state: 'user-speaking' }) } as MessageEvent)

    expect((client as unknown as { audio: HTMLAudioElement }).audio.muted).toBe(true)
    expect(audioInstances[0]?.pause).toHaveBeenCalledOnce()
    expect(audioInstances[0]?.srcObject).toBeNull()
    expect(outputEnergy).toEqual([0])
    dataChannel.onmessage?.({ data: JSON.stringify({ type: 'voice_state', state: 'speaking' }) } as MessageEvent)

    expect((client as unknown as { audio: HTMLAudioElement }).audio.muted).toBe(false)
    expect(audioInstances[0]?.srcObject).toBe(remoteStream)
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

describe('WebSocketVoiceClient', () => {
  it('admits only the replacement PCM after its assistant transcript starts', () => {
    const interruptPlayback = vi.fn()
    const playPCM = vi.fn()
    const onEvent = vi.fn()
    const client = Object.create(WebSocketVoiceClient.prototype) as {
      activeAudioUtteranceId: string | null
      cancelledAudioUtteranceIds: Set<string>
      awaitingAssistantTranscript: boolean
      interruptPlayback(): void
      playPCM(payload: ArrayBuffer): void
      onEvent(event: unknown): void
      handleMessage(message: MessageEvent): void
    }
    client.activeAudioUtteranceId = null
    client.cancelledAudioUtteranceIds = new Set()
    client.awaitingAssistantTranscript = false
    Object.assign(client, { interruptPlayback, playPCM, onEvent })

    client.handleMessage({
      data: JSON.stringify({ type: 'voice_state', state: 'user-speaking' }),
    } as MessageEvent)
    client.handleMessage({
      data: JSON.stringify({ type: 'audio_started', utterance_id: 'tts:old' }),
    } as MessageEvent)
    client.handleMessage({
      data: JSON.stringify({ type: 'audio_cancelled', utterance_id: 'tts:old' }),
    } as MessageEvent)
    client.handleMessage({ data: framedPCM('tts:old') } as MessageEvent)

    expect(interruptPlayback).toHaveBeenCalledOnce()
    expect(playPCM).not.toHaveBeenCalled()

    client.handleMessage({
      data: JSON.stringify({ type: 'transcript', state: 'thinking', role: 'assistant', phase: 'start', text: '' }),
    } as MessageEvent)
    client.handleMessage({
      data: JSON.stringify({ type: 'audio_started', utterance_id: 'tts:new' }),
    } as MessageEvent)
    client.handleMessage({ data: framedPCM('tts:new') } as MessageEvent)

    expect(playPCM).toHaveBeenCalledOnce()
    expect(onEvent).toHaveBeenCalledWith({ type: 'voice_state', state: 'user-speaking' })
  })

  it('clears scheduled PCM as soon as VAD reports that the user is speaking', () => {
    const previousGain = { disconnect: vi.fn() } as unknown as GainNode
    const replacementGain = { connect: vi.fn() } as unknown as GainNode
    const onEvent = vi.fn()
    const onOutputEnergy = vi.fn()
    const client = Object.create(WebSocketVoiceClient.prototype) as {
      context: Pick<AudioContext, 'currentTime' | 'createGain' | 'destination'>
      gain: GainNode | null
      nextPlaybackTime: number
      awaitingAssistantTranscript: boolean
      onEvent(event: unknown): void
      onOutputEnergy(energy: number): void
      handleMessage(message: MessageEvent): void
    }
    client.context = {
      currentTime: 4.2,
      createGain: () => replacementGain,
      destination: {} as AudioDestinationNode,
    }
    client.gain = previousGain
    client.nextPlaybackTime = 8.4
    client.awaitingAssistantTranscript = false
    Object.assign(client, { onEvent, onOutputEnergy })

    client.handleMessage({
      data: JSON.stringify({ type: 'voice_state', state: 'user-speaking' }),
    } as MessageEvent)

    expect(previousGain.disconnect).toHaveBeenCalledOnce()
    expect(replacementGain.connect).toHaveBeenCalledWith(client.context.destination)
    expect(client.nextPlaybackTime).toBe(4.2)
    expect(onOutputEnergy).toHaveBeenCalledWith(0)
    expect(onEvent).toHaveBeenCalledWith({ type: 'voice_state', state: 'user-speaking' })
  })
})

function framedPCM(utteranceId: string): ArrayBuffer {
  const identifier = new TextEncoder().encode(utteranceId)
  const packet = new Uint8Array(5 + identifier.length + 2)
  packet.set([65, 80, 67, 77, identifier.length], 0)
  packet.set(identifier, 5)
  packet.set([2, 0], 5 + identifier.length)
  return packet.buffer
}
