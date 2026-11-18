import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import { AvatarAcceptanceRecorder, type AvatarAcceptanceManualChecks } from './avatarAcceptance'
import { AvatarVideoClient, fetchAvatarStatus, stateFromAvatarStatus, type AvatarVideoState, type AvatarVideoStatus } from './avatarVideo'

type AvatarVideoPreviewProps = {
  csrfToken: string
  onAudioOwnershipChange: (active: boolean) => void
}

const stateLabels: Record<AvatarVideoState, string> = {
  disabled: '未启用',
  rebuilding: '重建中，暂不可用',
  unavailable: '服务不可用',
  ready: '可用',
  connecting: '连接中',
  connected: '已连接，等待首帧',
  playing: '正在显示视频',
  error: '连接失败',
}

export function AvatarVideoPreview({ csrfToken, onAudioOwnershipChange }: AvatarVideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const clientRef = useRef<AvatarVideoClient | null>(null)
  const recorderRef = useRef(new AvatarAcceptanceRecorder())
  const [status, setStatus] = useState<AvatarVideoStatus | null>(null)
  const [state, setState] = useState<AvatarVideoState>('disabled')
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState(false)
  const [videoVisible, setVideoVisible] = useState(false)
  const [acceptanceEnabled, setAcceptanceEnabled] = useState(false)
  const [acceptanceCurrent, setAcceptanceCurrent] = useState(recorderRef.current.current())
  const [acceptanceCompleted, setAcceptanceCompleted] = useState(0)
  const [manualChecks, setManualChecks] = useState<AvatarAcceptanceManualChecks>({
    watermark_absent: false,
    clarity_acceptable: false,
  })
  const videoLayerHost = document.querySelector<HTMLElement>('.workspace-shell')

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

  useEffect(() => {
    const beginCancellation = () => clientRef.current?.beginAcceptanceCancellation()
    document.addEventListener('arch:avatar-user-speaking', beginCancellation)
    return () => document.removeEventListener('arch:avatar-user-speaking', beginCancellation)
  }, [])

  function refreshAcceptance() {
    setAcceptanceCurrent({ ...recorderRef.current.current() })
    setAcceptanceCompleted(recorderRef.current.completedCount())
  }

  function resetAcceptance() {
    recorderRef.current = new AvatarAcceptanceRecorder()
    setAcceptanceCurrent({})
    setAcceptanceCompleted(0)
    setManualChecks({ watermark_absent: false, clarity_acceptable: false })
  }

  async function toggle() {
    if (busy) return
    setBusy(true)
    try {
      if (clientRef.current) {
        await clientRef.current.close()
        clientRef.current = null
        if (acceptanceEnabled) {
          recorderRef.current.resetCurrent()
          refreshAcceptance()
        }
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
      if (acceptanceEnabled) {
        recorderRef.current.beginSession()
        refreshAcceptance()
      }
      const client = new AvatarVideoClient(
        csrfToken,
        videoRef.current,
        setState,
        () => setVideoVisible(true),
        onAudioOwnershipChange,
        acceptanceEnabled ? {
          onCancellationStart: (timestampMs) => {
            recorderRef.current.beginCancellation(timestampMs)
            refreshAcceptance()
          },
          onFirstAudio: (elapsedMs) => {
            recorderRef.current.recordFirstAudio(elapsedMs)
            refreshAcceptance()
          },
          onFirstVideo: (elapsedMs) => {
            recorderRef.current.recordFirstVideo(elapsedMs)
            refreshAcceptance()
          },
          onIdleMarker: (videoPts) => {
            recorderRef.current.recordIdleMarker(videoPts)
            refreshAcceptance()
          },
          onVideoFrame: (timestampMs, mediaTimeSeconds) => {
            const beforeFps = recorderRef.current.current().fps
            const beforeCancellationMs = recorderRef.current.current().cancellation_ms
            recorderRef.current.recordVideoFrame(timestampMs, mediaTimeSeconds)
            const after = recorderRef.current.current()
            if (beforeFps !== after.fps || beforeCancellationMs !== after.cancellation_ms) {
              refreshAcceptance()
            }
          },
        } : undefined,
      )
      clientRef.current = client
      setActive(true)
      try {
        await client.connect()
      } catch (error) {
        await client.close()
        clientRef.current = null
        if (acceptanceEnabled) {
          recorderRef.current.resetCurrent()
          refreshAcceptance()
        }
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

  function commitObservation() {
    try {
      recorderRef.current.commit(manualChecks)
      setManualChecks({ watermark_absent: false, clarity_acceptable: false })
      refreshAcceptance()
    } catch {
      refreshAcceptance()
    }
  }

  function downloadObservations() {
    try {
      const blob = new Blob([recorderRef.current.exportJson()], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'avatar-device-observations.json'
      link.click()
      URL.revokeObjectURL(url)
    } catch {
      refreshAcceptance()
    }
  }

  const objectiveComplete = acceptanceCurrent.audio_first_ms !== undefined
    && acceptanceCurrent.video_first_ms !== undefined
    && acceptanceCurrent.fps !== undefined
    && acceptanceCurrent.cancellation_ms !== undefined

  const videoLayer = (
    <div className={`avatar-video-layer${videoVisible ? ' avatar-video-layer--live' : ''}`} aria-hidden="true">
      <video ref={videoRef} playsInline autoPlay />
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
          <button type="button" className="secondary-button" onClick={() => void toggle()} disabled={busy || (!status?.enabled && !active) || state === 'rebuilding' || state === 'unavailable'}>
            {busy ? '正在连接' : active ? '关闭视频' : '启用视频'}
          </button>
        </div>
        <p className="avatar-video-status" data-avatar-state={state}>{stateLabels[state]}{status?.detail ? `：${status.detail}` : ''}</p>
        <label>
          <input
            type="checkbox"
            checked={acceptanceEnabled}
            disabled={active}
            onChange={(event) => {
              setAcceptanceEnabled(event.target.checked)
              resetAcceptance()
            }}
          />
          目标设备验收
        </label>
        {acceptanceEnabled ? (
          <div className="avatar-acceptance" aria-live="polite">
            <p>{acceptanceCompleted}/20 轮已完成。每轮关闭视频后再重新启用。</p>
            {objectiveComplete ? (
              <>
                <label><input type="checkbox" checked={manualChecks.watermark_absent} onChange={(event) => setManualChecks((current) => ({ ...current, watermark_absent: event.target.checked }))} /> 无水印</label>
                <label><input type="checkbox" checked={manualChecks.clarity_acceptable} onChange={(event) => setManualChecks((current) => ({ ...current, clarity_acceptable: event.target.checked }))} /> 清晰度合格</label>
                <button type="button" className="secondary-button" disabled={!manualChecks.watermark_absent || !manualChecks.clarity_acceptable} onClick={commitObservation}>保存本轮</button>
              </>
            ) : <p>等待本轮首帧、30 帧率窗口和插话后的 idle 画面。</p>}
            {acceptanceCompleted === 20 ? <button type="button" className="secondary-button" onClick={downloadObservations}>下载验收 JSON</button> : null}
          </div>
        ) : null}
      </section>
      {videoLayerHost ? createPortal(videoLayer, videoLayerHost) : null}
    </>
  )
}
