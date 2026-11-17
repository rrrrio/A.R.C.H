export type AvatarVideoState = 'disabled' | 'rebuilding' | 'unavailable' | 'ready' | 'connecting' | 'connected' | 'playing' | 'error'

export type AvatarVideoStatus = {
  enabled: boolean
  state: string
  model: string
  source_video: string
  detail?: string | null
}

export function stateFromAvatarStatus(status: AvatarVideoStatus): AvatarVideoState {
  if (status.state === 'rebuilding') return 'rebuilding'
  if (!status.enabled) return 'disabled'
  return status.state === 'ready' ? 'ready' : 'unavailable'
}

type OfferResponse = { sdp: string; type: 'answer'; pc_id: string }

function isOfferResponse(value: unknown): value is OfferResponse {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.sdp === 'string'
    && candidate.type === 'answer'
    && typeof candidate.pc_id === 'string'
}

function isAvatarStatus(value: unknown): value is AvatarVideoStatus {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Record<string, unknown>
  return typeof candidate.enabled === 'boolean'
    && typeof candidate.state === 'string'
    && typeof candidate.model === 'string'
    && typeof candidate.source_video === 'string'
}

async function waitForIceGathering(connection: RTCPeerConnection): Promise<void> {
  if (connection.iceGatheringState === 'complete') return
  await new Promise<void>((resolve) => {
    const timeout = window.setTimeout(resolve, 3_000)
    connection.addEventListener('icegatheringstatechange', () => {
      if (connection.iceGatheringState === 'complete') {
        window.clearTimeout(timeout)
        resolve()
      }
    })
  })
}

export async function fetchAvatarStatus(): Promise<AvatarVideoStatus> {
  const response = await fetch('/api/avatar/status')
  const payload: unknown = await response.json()
  if (!response.ok || !isAvatarStatus(payload)) throw new Error('Avatar status unavailable.')
  return payload
}

export class AvatarVideoClient {
  private readonly connection = new RTCPeerConnection()
  private readonly csrfToken: string
  private readonly video: HTMLVideoElement
  private serverSessionMayExist = false
  private closing = false
  private connectionClosedNotified = false
  private videoFrameNotified = false
  private audioTimingNotified = false
  private videoTimingNotified = false
  private connectedAt = 0
  private hasAudioTrack = false
  private hasVideoTrack = false
  private audioOwnershipTransferred = false
  private remoteStream: MediaStream | null = null
  private readonly onState: (state: AvatarVideoState) => void
  private readonly onVideoFrame: () => void
  private readonly onAudioOwnership: (active: boolean) => void

  constructor(
    csrfToken: string,
    video: HTMLVideoElement,
    onState: (state: AvatarVideoState) => void = () => undefined,
    onVideoFrame: () => void = () => undefined,
    onAudioOwnership: (active: boolean) => void = () => undefined,
  ) {
    this.csrfToken = csrfToken
    this.video = video
    this.onState = onState
    this.onVideoFrame = onVideoFrame
    this.onAudioOwnership = onAudioOwnership
    this.video.muted = true
    this.video.autoplay = true
    this.video.playsInline = true
    this.connection.addTransceiver('audio', { direction: 'recvonly' })
    this.connection.addTransceiver('video', { direction: 'recvonly' })
    this.connection.ontrack = (event) => {
      if (!this.remoteStream) this.remoteStream = event.streams[0] ?? new MediaStream()
      if (event.streams.length === 0) this.remoteStream.addTrack(event.track)
      this.hasAudioTrack ||= event.track.kind === 'audio'
      this.hasVideoTrack ||= event.track.kind === 'video'
      if (event.track.kind === 'audio') {
        event.track.onunmute = () => this.notifyBrowserTiming('browser-first-audio-unmuted')
      }
      this.video.srcObject = this.remoteStream
      if (event.track.kind === 'video') {
        this.video.requestVideoFrameCallback(() => this.notifyVideoFrame())
      }
      this.completeMediaConnection()
    }
    this.connection.onconnectionstatechange = () => {
      const state = this.connection.connectionState
      if (this.closing || this.connectionClosedNotified) return
      if (state === 'failed' || state === 'closed' || state === 'disconnected') {
        this.connectionClosedNotified = true
        this.releaseAudioOwnership()
        this.onState('error')
      }
    }
  }

  async connect(): Promise<void> {
    this.onState('connecting')
    this.connectedAt = performance.now()
    const offer = await this.connection.createOffer()
    await this.connection.setLocalDescription(offer)
    await waitForIceGathering(this.connection)
    const local = this.connection.localDescription
    if (!local || local.type !== 'offer' || !local.sdp) throw new Error('Avatar offer was not created.')
    this.serverSessionMayExist = true
    const response = await fetch('/api/avatar/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
      body: JSON.stringify({ sdp: local.sdp, type: local.type }),
    })
    const payload: unknown = await response.json()
    if (!response.ok || !isOfferResponse(payload)) throw new Error('Avatar video connection failed.')
    await this.connection.setRemoteDescription(payload)
  }

  async close(): Promise<void> {
    this.closing = true
    this.videoFrameNotified = false
    this.video.pause()
    this.video.srcObject = null
    this.releaseAudioOwnership()
    this.connection.close()
    if (this.serverSessionMayExist) {
      await fetch('/api/avatar/session', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': this.csrfToken },
      }).catch(() => undefined)
    }
    this.serverSessionMayExist = false
  }

  private notifyVideoFrame(): void {
    if (this.closing || this.videoFrameNotified) return
    this.videoFrameNotified = true
    this.notifyBrowserTiming('browser-first-video-decoded')
    this.onVideoFrame()
    this.onState('playing')
  }

  private completeMediaConnection(): void {
    if (!this.hasAudioTrack || !this.hasVideoTrack || this.closing) return
    this.video.muted = false
    void this.video.play().catch(() => undefined)
    if (!this.audioOwnershipTransferred) {
      this.audioOwnershipTransferred = true
      this.onAudioOwnership(true)
    }
    this.onState('connected')
  }

  private releaseAudioOwnership(): void {
    if (!this.audioOwnershipTransferred) return
    this.audioOwnershipTransferred = false
    this.onAudioOwnership(false)
  }

  private notifyBrowserTiming(stage: 'browser-first-audio-unmuted' | 'browser-first-video-decoded'): void {
    const alreadyNotified = stage === 'browser-first-audio-unmuted'
      ? this.audioTimingNotified
      : this.videoTimingNotified
    if (this.closing || alreadyNotified) return
    if (stage === 'browser-first-audio-unmuted') this.audioTimingNotified = true
    else this.videoTimingNotified = true
    void fetch('/api/avatar/timing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
      body: JSON.stringify({ stage, elapsed_ms: Math.max(0, Math.round(performance.now() - this.connectedAt)) }),
    }).catch(() => undefined)
  }
}

export { isAvatarStatus, isOfferResponse }
