export type AvatarAcceptanceObservation = {
  audio_first_ms: number
  video_first_ms: number
  fps: number
  cancellation_ms: number
  watermark_absent: boolean
  clarity_acceptable: boolean
}

export type AvatarAcceptanceManualChecks = Pick<
  AvatarAcceptanceObservation,
  'watermark_absent' | 'clarity_acceptable'
>

type ObjectiveObservation = Omit<AvatarAcceptanceObservation, keyof AvatarAcceptanceManualChecks>

const REQUIRED_FRAME_COUNT = 30
const REQUIRED_OBSERVATION_COUNT = 20
const VIDEO_FPS = 25

/** Holds opt-in, timing-only device-acceptance data entirely in browser memory. */
export class AvatarAcceptanceRecorder {
  private readonly observations: AvatarAcceptanceObservation[] = []
  private draft: Partial<ObjectiveObservation> = {}
  private frameTimes: number[] = []
  private idleVideoPts: number | null = null
  private cancellationStartedAt: number | null = null

  beginSession() {
    if (this.observations.length >= REQUIRED_OBSERVATION_COUNT) throw new Error('All twenty observations are complete.')
    this.draft = {}
    this.frameTimes = []
    this.idleVideoPts = null
    this.cancellationStartedAt = null
  }

  resetCurrent() {
    this.draft = {}
    this.frameTimes = []
    this.idleVideoPts = null
    this.cancellationStartedAt = null
  }

  current(): Readonly<Partial<ObjectiveObservation>> {
    return this.draft
  }

  completedCount(): number {
    return this.observations.length
  }

  recordFirstAudio(elapsedMs: number) {
    if (this.draft.audio_first_ms === undefined) this.draft.audio_first_ms = nonNegativeInteger(elapsedMs)
  }

  recordFirstVideo(elapsedMs: number) {
    if (this.draft.video_first_ms === undefined) this.draft.video_first_ms = nonNegativeInteger(elapsedMs)
  }

  recordVideoFrame(timestampMs: number, mediaTimeSeconds: number) {
    if (this.frameTimes.length < REQUIRED_FRAME_COUNT) {
      this.frameTimes.push(timestampMs)
      if (this.frameTimes.length === REQUIRED_FRAME_COUNT) {
        const elapsed = this.frameTimes.at(-1)! - this.frameTimes[0]
        if (elapsed > 0) this.draft.fps = round((REQUIRED_FRAME_COUNT - 1) * 1_000 / elapsed)
      }
    }
    if (
      this.cancellationStartedAt !== null
      && this.idleVideoPts !== null
      && this.draft.cancellation_ms === undefined
      && mediaTimeSeconds >= this.idleVideoPts / VIDEO_FPS
    ) {
      this.draft.cancellation_ms = nonNegativeInteger(timestampMs - this.cancellationStartedAt)
    }
  }

  beginCancellation(timestampMs: number) {
    if (this.cancellationStartedAt === null && this.draft.cancellation_ms === undefined) {
      this.cancellationStartedAt = timestampMs
    }
  }

  recordIdleMarker(videoPts: number) {
    if (Number.isInteger(videoPts) && videoPts >= 0) this.idleVideoPts = videoPts
  }

  commit(manual: AvatarAcceptanceManualChecks): AvatarAcceptanceObservation {
    const { audio_first_ms, video_first_ms, fps, cancellation_ms } = this.draft
    if (
      audio_first_ms === undefined
      || video_first_ms === undefined
      || fps === undefined
      || cancellation_ms === undefined
    ) {
      throw new Error('The current observation is incomplete.')
    }
    if (this.observations.length >= REQUIRED_OBSERVATION_COUNT) throw new Error('All twenty observations are complete.')
    const observation = { audio_first_ms, video_first_ms, fps, cancellation_ms, ...manual }
    this.observations.push(observation)
    this.resetCurrent()
    return observation
  }

  exportJson(): string {
    if (this.observations.length !== REQUIRED_OBSERVATION_COUNT) {
      throw new Error('Exactly twenty complete observations are required.')
    }
    return JSON.stringify(this.observations, null, 2)
  }
}

function nonNegativeInteger(value: number): number {
  if (!Number.isFinite(value)) throw new Error('Timing must be finite.')
  return Math.max(0, Math.round(value))
}

function round(value: number): number {
  return Math.round(value * 100) / 100
}
