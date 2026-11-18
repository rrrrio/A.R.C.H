export type AvatarVideoState = 'disabled' | 'rebuilding' | 'unavailable' | 'ready' | 'connecting' | 'connected' | 'playing' | 'error'

export type AvatarVideoStatus = {
  enabled: boolean
  state: string
  model: string
  source_video: string
  detail?: string | null
}

export type AvatarAcceptanceCallbacks = {
  onCancellationStart: (timestampMs: number) => void
  onFirstAudio: (elapsedMs: number) => void
  onFirstVideo: (elapsedMs: number) => void
  onIdleMarker: (videoPts: number) => void
  onVideoFrame: (timestampMs: number, mediaTimeSeconds: number) => void
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
  private readonly acceptance: AvatarAcceptanceCallbacks | undefined
  private recorderChannel: RTCDataChannel | null = null
  private acceptanceSpeaking = false

  constructor(
    csrfToken: string,
    video: HTMLVideoElement,
    onState: (state: AvatarVideoState) => void = () => undefined,
    onVideoFrame: () => void = () => undefined,
    onAudioOwnership: (active: boolean) => void = () => undefined,
    acceptance?: AvatarAcceptanceCallbacks,
  ) {
    this.csrfToken = csrfToken
    this.video = video
    this.onState = onState
    this.onVideoFrame = onVideoFrame
    this.onAudioOwnership = onAudioOwnership
    this.acceptance = acceptance
    this.video.muted = true
    this.video.autoplay = true
    this.video.playsInline = true
    this.connection.addTransceiver('audio', { direction: 'recvonly' })
    this.connection.addTransceiver('video', { direction: 'recvonly' })
    if (acceptance) {
      this.recorderChannel = this.connection.createDataChannel('arch-avatar-acceptance-v1')
      this.recorderChannel.onmessage = (event) => this.handleRecorderMarker(event.data)
    }
    this.connection.ontrack = (event) => {
      if (!this.remoteStream) this.remoteStream = event.streams[0] ?? new MediaStream()
      if (event.streams.length === 0) this.remoteStream.addTrack(event.track)
      this.hasAudioTrack ||= event.track.kind === 'audio'
      this.hasVideoTrack ||= event.track.kind === 'video'
      if (event.track.kind === 'audio') {
        event.track.onunmute = () => {
          const elapsedMs = this.elapsedMs()
          if (this.acceptance) this.acceptance.onFirstAudio(elapsedMs)
          else this.notifyBrowserTiming('browser-first-audio-unmuted', elapsedMs)
        }
      }
      this.video.srcObject = this.remoteStream
      if (event.track.kind === 'video') {
        this.video.requestVideoFrameCallback((timestampMs, metadata) => this.notifyVideoFrame(timestampMs, metadata.mediaTime))
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
    this.recorderChannel?.close()
    this.recorderChannel = null
    if (this.serverSessionMayExist) {
      await fetch('/api/avatar/session', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': this.csrfToken },
      }).catch(() => undefined)
    }
    this.serverSessionMayExist = false
  }

  beginAcceptanceCancellation(): void {
    if (!this.videoFrameNotified || !this.acceptanceSpeaking || this.closing) return
    this.acceptanceSpeaking = false
    this.acceptance?.onCancellationStart(performance.now())
  }

  private notifyVideoFrame(timestampMs: number, mediaTimeSeconds: number): void {
    if (this.closing) return
    if (!this.videoFrameNotified) {
      this.videoFrameNotified = true
      const elapsedMs = this.elapsedMs()
      if (this.acceptance) this.acceptance.onFirstVideo(elapsedMs)
      else this.notifyBrowserTiming('browser-first-video-decoded', elapsedMs)
      this.onVideoFrame()
      this.onState('playing')
    }
    this.acceptance?.onVideoFrame(timestampMs, mediaTimeSeconds)
    if (this.acceptance) {
      this.video.requestVideoFrameCallback((nextTimestampMs, metadata) => {
        this.notifyVideoFrame(nextTimestampMs, metadata.mediaTime)
      })
    }
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

  private notifyBrowserTiming(
    stage: 'browser-first-audio-unmuted' | 'browser-first-video-decoded',
    elapsedMs = this.elapsedMs(),
  ): void {
    const alreadyNotified = stage === 'browser-first-audio-unmuted'
      ? this.audioTimingNotified
      : this.videoTimingNotified
    if (this.closing || alreadyNotified) return
    if (stage === 'browser-first-audio-unmuted') this.audioTimingNotified = true
    else this.videoTimingNotified = true
    void fetch('/api/avatar/timing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': this.csrfToken },
      body: JSON.stringify({ stage, elapsed_ms: elapsedMs }),
    }).catch(() => undefined)
  }

  private elapsedMs(): number {
    return Math.max(0, Math.round(performance.now() - this.connectedAt))
  }

  private handleRecorderMarker(data: unknown): void {
    if (typeof data !== 'string') return
    try {
      const parsed: unknown = JSON.parse(data)
      if (
        !parsed
        || typeof parsed !== 'object'
      ) return
      const marker = parsed as Record<string, unknown>
      if (marker.type === 'speaking') {
        this.acceptanceSpeaking = true
        return
      }
      if (marker.type === 'complete') {
        this.acceptanceSpeaking = false
        return
      }
      if (!Number.isInteger(marker.video_pts) || Number(marker.video_pts) < 0 || marker.type !== 'idle') return
      this.acceptance?.onIdleMarker(Number(marker.video_pts))
    } catch {
      // The opt-in data channel only accepts fixed timing markers.
    }
  }
}

export { isAvatarStatus, isOfferResponse }
