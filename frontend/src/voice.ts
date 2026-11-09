type OfferResponse = { sdp: string; type: 'answer'; pc_id: string }

type Candidate = { candidate: string; sdp_mid: string; sdp_mline_index: number }

export type VoiceActivity = 'user-speaking' | 'recognizing' | 'thinking' | 'speaking' | 'listening'
export type LatencyStage = 'speech-start' | 'speech-end' | 'asr-final' | 'llm-first-token' | 'tts-first-byte' | 'playback-first-audio' | 'playback-stopped'

export type VoiceEvent =
  | { type: 'voice_state'; state: VoiceActivity }
  | { type: 'transcript'; state: VoiceActivity; role: 'user' | 'assistant'; phase: 'final' | 'start' | 'delta' | 'end'; text: string }
  | { type: 'latency'; utterance_id: string; stage: LatencyStage; elapsed_ms: number }

export type AudioInput = { deviceId: string; label: string }

export class VoiceClient {
  private readonly connection = new RTCPeerConnection()
  private readonly audio = new Audio()
  private readonly csrfToken: string
  private readonly onEvent: (event: VoiceEvent) => void
  private readonly audioDeviceId: string | undefined
  private dataChannel: RTCDataChannel | null = null
  private pcId: string | null = null
  private pendingCandidates: Candidate[] = []
  private stream: MediaStream | null = null
  private serverSessionMayExist = false

  constructor(csrfToken: string, onEvent: (event: VoiceEvent) => void, audioDeviceId?: string) {
    this.csrfToken = csrfToken
    this.onEvent = onEvent
    this.audioDeviceId = audioDeviceId
    this.audio.autoplay = true
    this.connection.ontrack = (event) => {
      this.audio.srcObject = event.streams[0] ?? new MediaStream([event.track])
      void this.audio.play().catch(() => undefined)
    }
    this.connection.onicecandidate = (event) => {
      if (!event.candidate) return
      const candidate = event.candidate
      if (candidate.sdpMid === null || candidate.sdpMLineIndex === null) return
      const serialized: Candidate = {
        candidate: candidate.candidate,
        sdp_mid: candidate.sdpMid,
        sdp_mline_index: candidate.sdpMLineIndex,
      }
      void this.sendCandidates([serialized])
    }
  }

  async connect(): Promise<void> {
    const audio: MediaTrackConstraints | boolean = this.audioDeviceId
      ? { deviceId: { exact: this.audioDeviceId } }
      : true
    this.stream = await navigator.mediaDevices.getUserMedia({ audio, video: false })
    this.dataChannel = this.connection.createDataChannel('messages')
    this.dataChannel.onmessage = (message) => {
      if (typeof message.data !== 'string') return
      try {
        const event: unknown = JSON.parse(message.data)
        if (isVoiceEvent(event)) {
          if (event.type === 'voice_state' && event.state === 'user-speaking') {
            this.audio.muted = true
          }
          if (event.type === 'voice_state' && event.state === 'speaking') {
            this.audio.muted = false
          }
          this.onEvent(event)
        }
      } catch {
        // The data channel may also carry internal signaling messages.
      }
    }
    for (const track of this.stream.getAudioTracks()) this.connection.addTrack(track, this.stream)
    const offer = await this.connection.createOffer()
    await this.connection.setLocalDescription(offer)
    await waitForIceGathering(this.connection)
    const localDescription = this.connection.localDescription
    if (!localDescription || localDescription.type !== 'offer' || !localDescription.sdp) {
      throw new Error('Voice offer was not created.')
    }
    const answer = await this.requestOffer(localDescription)
    this.pcId = answer.pc_id
    await this.connection.setRemoteDescription(answer)
    if (this.pendingCandidates.length) await this.sendCandidates(this.pendingCandidates.splice(0))
  }

  async close(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.audio.pause()
    this.audio.srcObject = null
    this.dataChannel?.close()
    this.dataChannel = null
    this.connection.close()
    if (this.serverSessionMayExist) {
      await fetch('/api/voice/session', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': this.csrfToken },
      })
    }
    this.pcId = null
    this.serverSessionMayExist = false
  }

  private async requestOffer(offer: RTCSessionDescriptionInit): Promise<OfferResponse> {
    // A network failure can hide a server-created peer identifier from this client.
    this.serverSessionMayExist = true
    const response = await fetch('/api/voice/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
      body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
    })
    const payload: unknown = await response.json()
    if (!response.ok || !isOfferResponse(payload)) throw new Error('Voice connection failed.')
    return payload
  }

  private async sendCandidates(candidates: Candidate[]): Promise<void> {
    if (!this.pcId) {
      this.pendingCandidates.push(...candidates)
      return
    }
    const response = await fetch('/api/voice/candidates', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
      body: JSON.stringify({ pc_id: this.pcId, candidates }),
    })
    if (!response.ok) throw new Error('Voice ICE negotiation failed.')
  }
}

function waitForIceGathering(connection: RTCPeerConnection): Promise<void> {
  if (connection.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve) => {
    const timeout = setTimeout(finish, 5_000)
    function finish() {
      clearTimeout(timeout)
      connection.removeEventListener('icegatheringstatechange', onStateChange)
      resolve()
    }
    function onStateChange() {
      if (connection.iceGatheringState === 'complete') finish()
    }
    connection.addEventListener('icegatheringstatechange', onStateChange)
  })
}

export async function listAudioInputs(): Promise<AudioInput[]> {
  const devices = await navigator.mediaDevices.enumerateDevices()
  return devices
    .filter((device) => device.kind === 'audioinput')
    .map((device, index) => ({
      deviceId: device.deviceId,
      label: device.label || `麦克风 ${index + 1}`,
    }))
}

export function isOfferResponse(value: unknown): value is OfferResponse {
  return (
    typeof value === 'object' && value !== null && 'sdp' in value && 'type' in value && 'pc_id' in value &&
    typeof value.sdp === 'string' && value.type === 'answer' && typeof value.pc_id === 'string'
  )
}

export function isVoiceEvent(value: unknown): value is VoiceEvent {
  if (typeof value !== 'object' || value === null || !('type' in value) || typeof value.type !== 'string') {
    return false
  }
  if (value.type === 'voice_state') {
    return 'state' in value && isVoiceActivity(value.state)
  }
  if (value.type === 'latency') {
    return 'utterance_id' in value &&
      typeof value.utterance_id === 'string' &&
      'stage' in value &&
      isLatencyStage(value.stage) &&
      'elapsed_ms' in value &&
      typeof value.elapsed_ms === 'number' &&
      Number.isInteger(value.elapsed_ms) &&
      value.elapsed_ms >= 0
  }
  return (
    value.type === 'transcript' &&
    'state' in value &&
    isVoiceActivity(value.state) &&
    'role' in value &&
    (value.role === 'user' || value.role === 'assistant') &&
    'phase' in value &&
    (value.phase === 'final' || value.phase === 'start' || value.phase === 'delta' || value.phase === 'end') &&
    'text' in value &&
    typeof value.text === 'string'
  )
}

function isVoiceActivity(value: unknown): value is VoiceActivity {
  return value === 'user-speaking' || value === 'recognizing' || value === 'thinking' || value === 'speaking' || value === 'listening'
}

function isLatencyStage(value: unknown): value is LatencyStage {
  return value === 'speech-start' || value === 'speech-end' || value === 'asr-final' ||
    value === 'llm-first-token' || value === 'tts-first-byte' ||
    value === 'playback-first-audio' || value === 'playback-stopped'
}
