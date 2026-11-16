import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(new URL('./App.css', import.meta.url), 'utf8')

describe('workspace transcript layout', () => {
  it('keeps subtitle text out of the legacy two-column transcript grid', () => {
    expect(stylesheet).toMatch(/\.transcript__message p \{ display: block; grid-template-columns: none;/)
  })

  it('keeps the voice chamber stateful and disables its motion when requested', () => {
    expect(stylesheet).toContain('.voice-chamber--ready')
    expect(stylesheet).toContain('@media (prefers-reduced-motion: reduce)')
    expect(stylesheet).toContain('.workspace-navigation { background: #101923;')
  })

  it('uses a label-free collapsed navigation rail instead of clipped text or circular marks', () => {
    expect(stylesheet).toContain('.workspace-navigation__item span, .workspace-navigation__item small { display: none; }')
    expect(stylesheet).toContain('.workspace-navigation__item::before { width: 2px; height: 24px;')
    expect(stylesheet).not.toContain('.workspace-navigation__item::before { width: 18px; height: 18px; border: 1px solid currentColor; border-radius: 50%;')
  })

  it('uses a bounded, faded scroll window for growing management lists', () => {
    expect(stylesheet).toContain('max-height: min(52svh, 540px);')
    expect(stylesheet).toContain('mask-image: linear-gradient(to bottom, transparent 0')
  })

  it('adds three-dimensional roller depth to every shared list viewport', () => {
    expect(stylesheet).toContain('.roller-list { perspective: 900px; scroll-snap-type: y proximity; }')
    expect(stylesheet).toContain('scale(var(--roller-scale, .86)) rotateX(var(--roller-tilt, 0deg))')
    expect(stylesheet).toContain('padding-block: min(26svh, 270px);')
  })

  it('keeps native select controls within the dark workspace surface', () => {
    expect(stylesheet).toContain('.workspace-shell select option { background: #101923; color: var(--ink); }')
  })

  it('styles the native file picker button for the dark workspace', () => {
    expect(stylesheet).toContain(".workspace-shell input[type='file']::file-selector-button")
  })

  it('keeps login and pending-memory editing surfaces inside the dark theme', () => {
    expect(stylesheet).toContain('.setup-panel { background: #101923;')
    expect(stylesheet).toContain('.memory-candidate textarea { background: #16222d;')
    expect(stylesheet).toContain('.memory-candidate textarea:focus-visible')
  })

  it('synchronizes chamber ring and core colors for connection and activity states', () => {
    expect(stylesheet).toContain('.voice-chamber--connecting .voice-chamber__orbit, .voice-chamber--preparing .voice-chamber__orbit')
    expect(stylesheet).toContain('.voice-chamber--connecting .voice-chamber__core, .voice-chamber--preparing .voice-chamber__core')
    expect(stylesheet).toContain('.voice-chamber--error .voice-chamber__orbit, .voice-chamber--disconnected .voice-chamber__orbit')
    expect(stylesheet).toContain('.voice-chamber--error .voice-chamber__core, .voice-chamber--disconnected .voice-chamber__core')
    expect(stylesheet).toContain('.voice-chamber--ready.voice-chamber--thinking .voice-chamber__orbit')
    expect(stylesheet).toContain('.voice-chamber--ready.voice-chamber--thinking .voice-chamber__core')
  })
})
