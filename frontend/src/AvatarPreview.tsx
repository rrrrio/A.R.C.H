import { useEffect, useRef, useState } from 'react'

import type { BrowserAvatar } from './avatar'

type AvatarPreviewProps = {
  setEnergySink: (sink: (energy: number) => void) => void
}

/** Keep an optional local avatar model separate from the voice transport and server state. */
export function AvatarPreview({ setEnergySink }: AvatarPreviewProps) {
  const [enabled, setEnabled] = useState(false)
  const [loading, setLoading] = useState(false)
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState('')
  const host = useRef<HTMLDivElement | null>(null)
  const avatar = useRef<BrowserAvatar | null>(null)

  useEffect(() => {
    if (!enabled || host.current === null) {
      setEnergySink(() => undefined)
      return
    }
    let cancelled = false
    let preview: BrowserAvatar | null = null
    void import('./avatar')
      .then(({ BrowserAvatar: Renderer }) => {
        if (cancelled || host.current === null) return
        preview = new Renderer()
        preview.mount(host.current)
        avatar.current = preview
        setEnergySink((energy) => preview?.setEnergy(energy))
        setLoading(false)
      })
      .catch(() => {
        if (cancelled || host.current === null) return
        host.current.textContent = '此浏览器无法初始化本地 3D 预览。'
        setEnergySink(() => undefined)
        setLoading(false)
      })
    return () => {
      cancelled = true
      setEnergySink(() => undefined)
      preview?.dispose()
      avatar.current = null
    }
  }, [enabled, setEnergySink])

  async function chooseFile(file: File | null) {
    if (!file || avatar.current === null) return
    setError('')
    try {
      await avatar.current.load(file)
      setFileName(file.name)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '无法加载该 VRM 文件。')
    }
  }

  return (
    <section className="avatar-preview" aria-labelledby="avatar-title">
      <div className="avatar-preview__heading">
        <div>
          <h2 id="avatar-title">浏览器数字人</h2>
          <p>模型只保留在此浏览器，不会上传到 A.R.C.H.。</p>
        </div>
        <label className="toggle-control">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => {
              setError('')
              if (!event.target.checked) setFileName('')
              setLoading(event.target.checked)
              setEnabled(event.target.checked)
            }}
          />
          <span>启用预览</span>
        </label>
      </div>
      {enabled ? (
        <>
          <div className="avatar-preview__canvas" ref={host} aria-label="本地数字人预览" />
          <label className="avatar-preview__file" htmlFor="avatar-model">
            <span>{fileName || '选择本地 VRM 模型'}</span>
            <input id="avatar-model" type="file" accept=".vrm,model/vrm" disabled={loading} onChange={(event) => void chooseFile(event.target.files?.[0] ?? null)} />
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </>
      ) : null}
    </section>
  )
}
