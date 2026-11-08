type OfferResponse = { sdp: string; type: 'answer'; pc_id: string }

type Candidate = { candidate: string; sdp_mid: string; sdp_mline_index: number }

export class VoiceClient {
  private readonly connection = new RTCPeerConnection()
  private readonly audio = new Audio()
  private readonly csrfToken: string
  private pcId: string | null = null
  private pendingCandidates: Candidate[] = []
  private stream: MediaStream | null = null
  private serverSessionMayExist = false

  constructor(csrfToken: string) {
    this.csrfToken = csrfToken
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
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
    for (const track of this.stream.getAudioTracks()) this.connection.addTrack(track, this.stream)
    const offer = await this.connection.createOffer()
    await this.connection.setLocalDescription(offer)
    const answer = await this.requestOffer(offer)
    this.pcId = answer.pc_id
    await this.connection.setRemoteDescription(answer)
    if (this.pendingCandidates.length) await this.sendCandidates(this.pendingCandidates.splice(0))
  }

  async close(): Promise<void> {
    this.stream?.getTracks().forEach((track) => track.stop())
    this.stream = null
    this.audio.pause()
    this.audio.srcObject = null
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

export function isOfferResponse(value: unknown): value is OfferResponse {
  return (
    typeof value === 'object' && value !== null && 'sdp' in value && 'type' in value && 'pc_id' in value &&
    typeof value.sdp === 'string' && value.type === 'answer' && typeof value.pc_id === 'string'
  )
}
