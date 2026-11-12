export type AvatarVideoState = 'disabled' | 'unavailable' | 'ready' | 'connecting' | 'connected' | 'error'

export type AvatarVideoStatus = {
  enabled: boolean
  state: string
  model: string
  source_video: string
  detail?: string | null
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
  private readonly onState: (state: AvatarVideoState) => void

  constructor(
    csrfToken: string,
    video: HTMLVideoElement,
    onState: (state: AvatarVideoState) => void = () => undefined,
  ) {
    this.csrfToken = csrfToken
    this.video = video
    this.onState = onState
    this.video.muted = true
    this.video.autoplay = true
    this.video.playsInline = true
    this.connection.addTransceiver('video', { direction: 'recvonly' })
    this.connection.ontrack = (event) => {
      if (event.track.kind !== 'video') return
      this.video.srcObject = event.streams[0] ?? new MediaStream([event.track])
      void this.video.play().catch(() => undefined)
      this.onState('connected')
    }
    this.connection.onconnectionstatechange = () => {
      const state = this.connection.connectionState
      if (this.closing || this.connectionClosedNotified) return
      if (state === 'failed' || state === 'closed' || state === 'disconnected') {
        this.connectionClosedNotified = true
        this.onState('error')
      }
    }
  }

  async connect(): Promise<void> {
    this.onState('connecting')
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
    this.video.pause()
    this.video.srcObject = null
    this.connection.close()
    if (this.serverSessionMayExist) {
      await fetch('/api/avatar/session', {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': this.csrfToken },
      }).catch(() => undefined)
    }
    this.serverSessionMayExist = false
  }
}

export { isAvatarStatus, isOfferResponse }
