import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(new URL('./App.css', import.meta.url), 'utf8')

describe('workspace transcript layout', () => {
  it('keeps subtitle text out of the legacy two-column transcript grid', () => {
    expect(stylesheet).toMatch(/\.transcript__message p \{ display: block; grid-template-columns: none;/)
  })
})
