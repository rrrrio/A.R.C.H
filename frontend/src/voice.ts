type OfferResponse = { sdp: string; type: 'answer'; pc_id: string }

type Candidate = { candidate: string; sdp_mid: string; sdp_mline_index: number }

export type VoiceActivity = 'user-speaking' | 'recognizing' | 'thinking' | 'speaking' | 'listening'
export type LatencyStage = 'speech-start' | 'speech-end' | 'asr-final' | 'llm-first-token' | 'tts-first-byte' | 'playback-first-audio' | 'playback-stopped'

export type VoiceEvent =
  | { type: 'voice_ready' }
  | { type: 'voice_input_ready' }
  | { type: 'memory_candidates_ready' }
  | { type: 'voice_state'; state: VoiceActivity }
  | { type: 'transcript'; state: VoiceActivity; role: 'user' | 'assistant'; phase: 'final' | 'start' | 'delta' | 'end'; text: string }
  | { type: 'latency'; utterance_id: string; stage: LatencyStage; elapsed_ms: number }

type AudioGenerationEvent = {
  type: 'audio_started' | 'audio_cancelled'
  utterance_id: string
}

export type AudioInput = { deviceId: string; label: string }
export type OutputEnergyHandler = (energy: number) => void

export interface VoiceConnection {
  connect(): Promise<void>
  close(): Promise<void>
}

export class VoiceClient {
  private readonly connection = new RTCPeerConnection()
  private readonly audio = new Audio()
  private readonly csrfToken: string
  private readonly onEvent: (event: VoiceEvent) => void
  private readonly audioDeviceId: string | undefined
  private readonly onConnectionClosed: () => void
  private readonly onOutputEnergy: OutputEnergyHandler
  private dataChannel: RTCDataChannel | null = null
  private pcId: string | null = null
  private pendingCandidates: Candidate[] = []
  private stream: MediaStream | null = null
  private remoteAudioStream: MediaStream | null = null
  private serverSessionMayExist = false
  private closing = false
  private connectionClosedNotified = false
  private outputEnergyTimer: number | null = null
  private outputEnergyPolling = false
  private outputEnergySuppressed = false
  private remotePlaybackInterrupted = false

  constructor(
    csrfToken: string,
    onEvent: (event: VoiceEvent) => void,
    audioDeviceId?: string,
    onConnectionClosed: () => void = () => undefined,
    onOutputEnergy: OutputEnergyHandler = () => undefined,
  ) {
    this.csrfToken = csrfToken
    this.onEvent = onEvent
    this.audioDeviceId = audioDeviceId
    this.onConnectionClosed = onConnectionClosed
    this.onOutputEnergy = onOutputEnergy
    this.audio.autoplay = true
    this.connection.ontrack = (event) => {
      this.remoteAudioStream = event.streams[0] ?? new MediaStream([event.track])
      this.resumeRemotePlayback()
      this.startOutputEnergyMeter()
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
    this.connection.onconnectionstatechange = () => {
      const state = this.connection.connectionState
      if (this.closing || this.connectionClosedNotified) return
      if (state === 'closed' || state === 'disconnected' || state === 'failed') {
        this.connectionClosedNotified = true
        this.onConnectionClosed()
      }
    }
  }

  async connect(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: voiceCaptureConstraints(this.audioDeviceId),
      video: false,
    })
    this.dataChannel = this.connection.createDataChannel('messages')
    this.dataChannel.onmessage = (message) => {
      if (typeof message.data !== 'string') return
      try {
        const event: unknown = JSON.parse(message.data)
        if (isVoiceEvent(event)) {
          if (event.type === 'voice_state' && event.state === 'user-speaking') {
            this.interruptRemotePlayback()
            this.outputEnergySuppressed = true
            this.onOutputEnergy(0)
          }
          if (event.type === 'voice_state' && event.state === 'speaking') {
            this.remotePlaybackInterrupted = false
            this.resumeRemotePlayback()
            this.outputEnergySuppressed = false
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
    this.closing = true
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.audio.pause()
    this.audio.srcObject = null
    this.remoteAudioStream = null
    this.dataChannel?.close()
    this.dataChannel = null
    this.connection.close()
    this.stopOutputEnergyMeter()
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

  private interruptRemotePlayback() {
    this.remotePlaybackInterrupted = true
    this.audio.muted = true
    this.audio.pause()
    this.audio.srcObject = null
  }

  private resumeRemotePlayback() {
    if (this.remotePlaybackInterrupted || !this.remoteAudioStream) return
    this.audio.srcObject = this.remoteAudioStream
    this.audio.muted = false
    void this.audio.play().catch(() => undefined)
  }

  private startOutputEnergyMeter() {
    this.stopOutputEnergyMeter()
    this.outputEnergyTimer = window.setInterval(() => void this.reportOutputEnergy(), 80)
  }

  private stopOutputEnergyMeter() {
    if (this.outputEnergyTimer !== null) window.clearInterval(this.outputEnergyTimer)
    this.outputEnergyTimer = null
    this.onOutputEnergy(0)
  }

  private async reportOutputEnergy() {
    if (this.outputEnergySuppressed) {
      this.onOutputEnergy(0)
      return
    }
    if (this.outputEnergyPolling || typeof this.connection.getStats !== 'function') return
    this.outputEnergyPolling = true
    try {
      const reports = await this.connection.getStats()
      for (const report of reports.values()) {
        if (report.type !== 'inbound-rtp') continue
        const candidate = report as RTCStats & { kind?: unknown; mediaType?: unknown; audioLevel?: unknown }
        if (candidate.kind !== 'audio' && candidate.mediaType !== 'audio') continue
        this.onOutputEnergy(normalizeAudioLevel(candidate.audioLevel))
        return
      }
      this.onOutputEnergy(0)
    } finally {
      this.outputEnergyPolling = false
    }
  }
}

export class WebSocketVoiceClient implements VoiceConnection {
  private readonly csrfToken: string
  private readonly onEvent: (event: VoiceEvent) => void
  private readonly audioDeviceId: string | undefined
  private readonly onConnectionClosed: () => void
  private socket: WebSocket | null = null
  private stream: MediaStream | null = null
  private context: AudioContext | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private processor: ScriptProcessorNode | null = null
  private gain: GainNode | null = null
  private captureSink: GainNode | null = null
  private nextPlaybackTime = 0
  private closing = false
  private readonly onOutputEnergy: OutputEnergyHandler
  private activeAudioUtteranceId: string | null = null
  private readonly cancelledAudioUtteranceIds = new Set<string>()
  // After the user barges in, PCM is admitted only once the visible assistant
  // transcript for the replacement response has begun.
  private awaitingAssistantTranscript = false

  constructor(
    csrfToken: string,
    onEvent: (event: VoiceEvent) => void,
    audioDeviceId?: string,
    onConnectionClosed: () => void = () => undefined,
    onOutputEnergy: OutputEnergyHandler = () => undefined,
  ) {
    this.csrfToken = csrfToken
    this.onEvent = onEvent
    this.audioDeviceId = audioDeviceId
    this.onConnectionClosed = onConnectionClosed
    this.onOutputEnergy = onOutputEnergy
  }

  async connect(): Promise<void> {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const socket = new WebSocket(
      `${protocol}//${location.host}/api/voice/stream`,
      `arch-csrf.${this.csrfToken}`,
    )
    socket.binaryType = 'arraybuffer'
    await new Promise<void>((resolve, reject) => {
      socket.onopen = () => resolve()
      socket.onerror = () => reject(new Error('Voice fallback connection failed.'))
    })
    this.socket = socket
    socket.onmessage = (message) => this.handleMessage(message)
    socket.onclose = () => {
      if (!this.closing) this.onConnectionClosed()
    }
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: voiceCaptureConstraints(this.audioDeviceId),
      video: false,
    })
    this.context = new AudioContext()
    await this.context.resume()
    this.gain = this.context.createGain()
    this.gain.connect(this.context.destination)
    this.captureSink = createMutedCaptureSink(this.context)
    this.source = this.context.createMediaStreamSource(this.stream)
    this.processor = this.context.createScriptProcessor(2048, 1, 1)
    this.processor.onaudioprocess = (event) => {
      if (socket.readyState !== WebSocket.OPEN) return
      socket.send(floatToPCM16(resample(event.inputBuffer.getChannelData(0), this.context?.sampleRate ?? 16000, 16000)))
    }
    this.source.connect(this.processor)
    this.processor.connect(this.captureSink)
  }

  async close(): Promise<void> {
    this.closing = true
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.processor?.disconnect()
    this.source?.disconnect()
    this.gain?.disconnect()
    this.captureSink?.disconnect()
    this.processor = null
    this.source = null
    this.gain = null
    this.captureSink = null
    await this.context?.close()
    this.context = null
    this.socket?.close()
    this.socket = null
    this.onOutputEnergy(0)
  }

  private handleMessage(message: MessageEvent) {
    if (typeof message.data === 'string') {
      try {
        const event: unknown = JSON.parse(message.data)
        if (isAudioGenerationEvent(event)) {
          this.handleAudioGeneration(event)
        } else if (isVoiceEvent(event)) {
          // The VAD state is the earliest browser-visible cancellation signal. Do not wait
          // for a later transport interruption frame while old PCM remains scheduled.
          if (event.type === 'voice_state' && event.state === 'user-speaking') {
            this.activeAudioUtteranceId = null
            this.awaitingAssistantTranscript = true
            this.interruptPlayback()
          }
          if (event.type === 'transcript' && event.role === 'assistant' && event.phase === 'start') {
            this.awaitingAssistantTranscript = false
          }
          this.onEvent(event)
        } else if (isAudioInterrupted(event)) this.interruptPlayback()
      } catch { /* Ignore invalid server messages. */ }
      return
    }
    if (message.data instanceof ArrayBuffer) {
      const packet = decodeBrowserPCM(message.data)
      if (packet && this.activeAudioUtteranceId === packet.utteranceId) {
        this.playPCM(packet.pcm)
      }
    }
  }

  private handleAudioGeneration(event: AudioGenerationEvent) {
    if (event.type === 'audio_cancelled') {
      this.cancelledAudioUtteranceIds.add(event.utterance_id)
      if (this.activeAudioUtteranceId === event.utterance_id) {
        this.activeAudioUtteranceId = null
        this.interruptPlayback()
      }
      return
    }
    if (!this.awaitingAssistantTranscript && !this.cancelledAudioUtteranceIds.has(event.utterance_id)) {
      this.activeAudioUtteranceId = event.utterance_id
    }
  }

  private playPCM(payload: ArrayBuffer) {
    if (!this.context || !this.gain) return
    const input = new Int16Array(payload)
    this.onOutputEnergy(pcmEnergy(input))
    const buffer = this.context.createBuffer(1, input.length, 24000)
    const samples = buffer.getChannelData(0)
    for (let index = 0; index < input.length; index += 1) samples[index] = input[index] / 32768
    const source = this.context.createBufferSource()
    source.buffer = buffer
    source.connect(this.gain)
    const start = Math.max(this.context.currentTime, this.nextPlaybackTime)
    source.start(start)
    this.nextPlaybackTime = start + buffer.duration
  }

  private interruptPlayback() {
    if (!this.context || !this.gain) return
    this.gain.disconnect()
    this.gain = this.context.createGain()
    this.gain.connect(this.context.destination)
    this.nextPlaybackTime = this.context.currentTime
    this.onOutputEnergy(0)
  }
}

export function normalizeAudioLevel(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
}

export function pcmEnergy(input: Int16Array): number {
  if (input.length === 0) return 0
  let squared = 0
  for (const sample of input) squared += (sample / 32768) ** 2
  return Math.min(1, Math.sqrt(squared / input.length) * 2.5)
}

export function decodeBrowserPCM(payload: ArrayBuffer): { utteranceId: string; pcm: ArrayBuffer } | null {
  const bytes = new Uint8Array(payload)
  if (bytes.length < 6 || String.fromCharCode(...bytes.subarray(0, 4)) !== 'APCM') return null
  const identifierLength = bytes[4]
  const audioStart = 5 + identifierLength
  if (!identifierLength || audioStart >= bytes.length || (bytes.length - audioStart) % 2) return null
  const utteranceId = new TextDecoder().decode(bytes.subarray(5, audioStart))
  if (!utteranceId) return null
  return { utteranceId, pcm: bytes.slice(audioStart).buffer }
}

/** Keep microphone processing active without monitoring it through the browser speakers. */
export function createMutedCaptureSink(
  context: Pick<AudioContext, 'createGain' | 'destination'>,
): GainNode {
  const sink = context.createGain()
  sink.gain.value = 0
  sink.connect(context.destination)
  return sink
}

/** Request browser-side voice processing while retaining an explicitly chosen microphone. */
export function voiceCaptureConstraints(audioDeviceId?: string): MediaTrackConstraints {
  return {
    ...(audioDeviceId ? { deviceId: { exact: audioDeviceId } } : {}),
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  }
}

function resample(input: Float32Array, inputRate: number, outputRate: number): Float32Array {
  if (inputRate === outputRate) return input
  const output = new Float32Array(Math.round(input.length * outputRate / inputRate))
  const step = inputRate / outputRate
  for (let index = 0; index < output.length; index += 1) output[index] = input[Math.min(input.length - 1, Math.floor(index * step))]
  return output
}

function floatToPCM16(input: Float32Array): ArrayBuffer {
  const output = new Int16Array(input.length)
  for (let index = 0; index < input.length; index += 1) output[index] = Math.max(-1, Math.min(1, input[index])) * 32767
  return output.buffer
}

function isAudioInterrupted(value: unknown): boolean {
  return typeof value === 'object' && value !== null && 'type' in value && value.type === 'audio_interrupted'
}

function isAudioGenerationEvent(value: unknown): value is AudioGenerationEvent {
  return typeof value === 'object' && value !== null && 'type' in value &&
    (value.type === 'audio_started' || value.type === 'audio_cancelled') &&
    'utterance_id' in value && typeof value.utterance_id === 'string'
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
  if (value.type === 'voice_ready' || value.type === 'voice_input_ready' || value.type === 'memory_candidates_ready') return Object.keys(value).length === 1
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
