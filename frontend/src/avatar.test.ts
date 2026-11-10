import { describe, expect, it } from 'vitest'

import { boundAvatarEnergy, isVrmFile } from './avatar'

describe('browser avatar helpers', () => {
  it('accepts only a VRM filename and bounds untrusted energy values', () => {
    expect(isVrmFile({ name: 'assistant.VRM' })).toBe(true)
    expect(isVrmFile({ name: 'assistant.glb' })).toBe(false)
    expect(boundAvatarEnergy(0.25)).toBe(0.25)
    expect(boundAvatarEnergy(8)).toBe(1)
    expect(boundAvatarEnergy(Number.NaN)).toBe(0)
  })
})
