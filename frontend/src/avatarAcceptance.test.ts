import { describe, expect, it } from 'vitest'

import { AvatarAcceptanceRecorder } from './avatarAcceptance'

describe('AvatarAcceptanceRecorder', () => {
  it('waits for the idle marker PTS before completing cancellation timing', () => {
    const recorder = new AvatarAcceptanceRecorder()
    recorder.beginSession()
    recorder.recordFirstAudio(101)
    recorder.recordFirstVideo(117)
    for (let index = 0; index < 30; index += 1) recorder.recordVideoFrame(index * 40, index / 25)

    recorder.beginCancellation(1_000)
    recorder.recordVideoFrame(1_120, 8.0)
    recorder.recordIdleMarker(201)
    recorder.recordVideoFrame(1_150, 8.03)
    expect(recorder.current().cancellation_ms).toBeUndefined()

    recorder.recordVideoFrame(1_180, 8.04)
    expect(recorder.current()).toMatchObject({
      audio_first_ms: 101,
      video_first_ms: 117,
      fps: 25,
      cancellation_ms: 180,
    })
  })

  it('exports only twenty complete timing-only observations', () => {
    const recorder = new AvatarAcceptanceRecorder()
    for (let index = 0; index < 20; index += 1) {
      recorder.beginSession()
      recorder.recordFirstAudio(100)
      recorder.recordFirstVideo(120)
      for (let frame = 0; frame < 30; frame += 1) recorder.recordVideoFrame(frame * 40, frame / 25)
      recorder.beginCancellation(2_000)
      recorder.recordIdleMarker(51)
      recorder.recordVideoFrame(2_150, 2.04)
      recorder.commit({ watermark_absent: true, clarity_acceptable: true })
    }

    expect(JSON.parse(recorder.exportJson())).toHaveLength(20)
    expect(() => recorder.beginSession()).toThrow('twenty')
  })

  it('rejects manual completion until all objective timings are recorded', () => {
    const recorder = new AvatarAcceptanceRecorder()
    recorder.beginSession()

    expect(() => recorder.commit({ watermark_absent: true, clarity_acceptable: true })).toThrow('incomplete')
  })
})
