import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { AvatarVideoClient, fetchAvatarStatus, stateFromAvatarStatus, type AvatarVideoState, type AvatarVideoStatus } from './avatarVideo'

type AvatarVideoPreviewProps = { csrfToken: string }

const stateLabels: Record<AvatarVideoState, string> = {
  disabled: '未启用',
  unavailable: '服务不可用',
  ready: '可用',
  connecting: '连接中',
  connected: '已连接，等待首帧',
  playing: '正在显示视频',
  error: '连接失败',
}

export function AvatarVideoPreview({ csrfToken }: AvatarVideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const clientRef = useRef<AvatarVideoClient | null>(null)
  const [status, setStatus] = useState<AvatarVideoStatus | null>(null)
  const [state, setState] = useState<AvatarVideoState>('disabled')
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState(false)
  const [videoVisible, setVideoVisible] = useState(false)

  useEffect(() => {
    let active = true
    void fetchAvatarStatus()
      .then((next) => {
        if (!active) return
        setStatus(next)
        setState(stateFromAvatarStatus(next))
      })
      .catch(() => active && setState('unavailable'))
    return () => {
      active = false
      void clientRef.current?.close()
      clientRef.current = null
    }
  }, [])

  async function toggle() {
    if (busy) return
    setBusy(true)
    try {
      if (clientRef.current) {
        await clientRef.current.close()
        clientRef.current = null
        setActive(false)
        setVideoVisible(false)
        setState(status?.enabled ? 'ready' : 'disabled')
        return
      }
      if (!status?.enabled || !videoRef.current) {
        setState('unavailable')
        return
      }
      setVideoVisible(false)
      const client = new AvatarVideoClient(
        csrfToken,
        videoRef.current,
        setState,
        () => setVideoVisible(true),
      )
      clientRef.current = client
      setActive(true)
      try {
        await client.connect()
      } catch (error) {
        await client.close()
        clientRef.current = null
        setActive(false)
        setVideoVisible(false)
        setState('error')
        throw error
      }
    } catch {
      setState('error')
    } finally {
      setBusy(false)
    }
  }

  const videoLayer = (
    <div className={`avatar-video-layer${videoVisible ? ' avatar-video-layer--live' : ''}`} aria-hidden="true">
      <video ref={videoRef} muted playsInline autoPlay />
    </div>
  )

  return (
    <>
      <section className="avatar-video-control" aria-labelledby="avatar-video-title">
        <div className="avatar-video-control-row">
          <div>
            <p className="eyebrow">真人视频</p>
            <h2 id="avatar-video-title">{status?.model ?? 'MuseTalk 1.5'}</h2>
          </div>
          <button type="button" className="secondary-button" onClick={() => void toggle()} disabled={busy || (!status?.enabled && !active) || state === 'unavailable'}>
            {busy ? '正在连接' : active ? '关闭视频' : '启用视频'}
          </button>
        </div>
        <p className="avatar-video-status" data-avatar-state={state}>{stateLabels[state]}{status?.detail ? `：${status.detail}` : ''}</p>
      </section>
      {createPortal(videoLayer, document.body)}
    </>
  )
}
