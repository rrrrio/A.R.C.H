import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'

import { AvatarPreview } from './AvatarPreview'
import { fetchAuthStatus, fetchCsrfToken, login, logout } from './auth'
import { fetchKnowledgeStatus, rebuildKnowledgeIndex, type KnowledgeStatus } from './knowledge'
import { fetchMemoryCandidates, type MemoryCandidate, updateMemoryCandidate } from './memory'
import { decideToolApproval, fetchToolApprovals, type ToolApproval } from './tools'
import { listAudioInputs, type AudioInput, type LatencyStage, type VoiceActivity, type VoiceConnection, type VoiceEvent, VoiceClient, WebSocketVoiceClient } from './voice'
import { createVoiceProfile, deleteVoiceProfile, fetchVoiceProfiles, selectVoiceProfile, type VoiceProfile } from './voices'
import './App.css'

type SessionState =
  | { kind: 'loading' }
  | { kind: 'unconfigured' }
  | { kind: 'anonymous' }
  | { kind: 'signing-in' }
  | { kind: 'authenticated'; csrfToken: string }
  | { kind: 'error' }

type ConversationState = {
  activity: VoiceActivity
  userText: string
  assistantText: string
  latency: { stage: LatencyStage; elapsedMs: number }[]
}

const initialConversation: ConversationState = { activity: 'listening', userText: '', assistantText: '', latency: [] }
const latencyHistoryLimit = 8

const activityLabels: Record<VoiceActivity, string> = {
  'user-speaking': '正在聆听',
  recognizing: '正在识别',
  thinking: '正在思考',
  speaking: '正在播报',
  listening: '等待说话',
}

const latencyLabels: Record<LatencyStage, string> = {
  'speech-start': '检测开始',
  'speech-end': '检测结束',
  'asr-final': '识别完成',
  'llm-first-token': '首个文本',
  'tts-first-byte': '首段语音',
  'playback-first-audio': '发送首段音频',
  'playback-stopped': '播放停止',
}

function App() {
  const [session, setSession] = useState<SessionState>({ kind: 'loading' })
  const [password, setPassword] = useState('')
  const [voiceState, setVoiceState] = useState<'idle' | 'connecting' | 'preparing' | 'ready' | 'disconnected' | 'error'>('idle')
  const [conversation, setConversation] = useState<ConversationState>(initialConversation)
  const [audioInputs, setAudioInputs] = useState<AudioInput[]>([])
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [pendingCandidates, setPendingCandidates] = useState<MemoryCandidate[]>([])
  const [approvedCandidates, setApprovedCandidates] = useState<MemoryCandidate[]>([])
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null)
  const [knowledgeIndexing, setKnowledgeIndexing] = useState(false)
  const [knowledgeIndexMessage, setKnowledgeIndexMessage] = useState<string | null>(null)
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, string>>({})
  const [candidateActionId, setCandidateActionId] = useState<string | null>(null)
  const [toolApprovals, setToolApprovals] = useState<ToolApproval[]>([])
  const [toolActionId, setToolActionId] = useState<string | null>(null)
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([])
  const [selectedVoiceProfileId, setSelectedVoiceProfileId] = useState<string | null>(null)
  const [profileName, setProfileName] = useState('')
  const [profileTranscript, setProfileTranscript] = useState('')
  const [profileReference, setProfileReference] = useState<File | null>(null)
  const [profileOwnershipConfirmed, setProfileOwnershipConfirmed] = useState(false)
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileError, setProfileError] = useState(false)
  const voiceClient = useRef<VoiceConnection | null>(null)
  const inputReadyTimer = useRef<number | null>(null)
  const avatarEnergySink = useRef<(energy: number) => void>(() => undefined)

  const setAvatarEnergySink = useCallback((sink: (energy: number) => void) => {
    avatarEnergySink.current = sink
  }, [])

  async function refreshMemoryCandidates() {
    try {
      const [pending, approved] = await Promise.all([
        fetchMemoryCandidates('pending'),
        fetchMemoryCandidates('approved'),
      ])
      setPendingCandidates(pending)
      setApprovedCandidates(approved)
      setCandidateDrafts(Object.fromEntries(pending.map((candidate) => [candidate.id, candidate.content])))
    } catch {
      setPendingCandidates([])
      setApprovedCandidates([])
    }
  }

  async function refreshKnowledgeStatus() {
    try {
      setKnowledgeStatus(await fetchKnowledgeStatus())
    } catch {
      setKnowledgeStatus(null)
    }
  }

  async function rebuildKnowledgeIndexFromBrowser() {
    if (session.kind !== 'authenticated' || knowledgeIndexing) return
    setKnowledgeIndexing(true)
    setKnowledgeIndexMessage(null)
    try {
      const result = await rebuildKnowledgeIndex(session.csrfToken)
      setKnowledgeStatus(result)
      setKnowledgeIndexMessage(`已更新 ${result.changed_source_count} 个文件。`)
    } catch {
      setKnowledgeIndexMessage('无法建立索引。请确认本地模型已准备完成后重试。')
    } finally {
      setKnowledgeIndexing(false)
    }
  }

  async function refreshToolApprovals() {
    try {
      setToolApprovals(await fetchToolApprovals())
    } catch {
      setToolApprovals([])
    }
  }

  async function refreshVoiceProfiles() {
    try {
      const profiles = await fetchVoiceProfiles()
      setVoiceProfiles(profiles.profiles)
      setSelectedVoiceProfileId(profiles.selected_id)
    } catch {
      setVoiceProfiles([])
      setSelectedVoiceProfileId(null)
    }
  }

  async function refreshSession() {
    try {
      const status = await fetchAuthStatus()
      if (!status.configured) {
        setSession({ kind: 'unconfigured' })
      } else if (status.authenticated) {
        const csrfToken = await fetchCsrfToken()
        setSession({ kind: 'authenticated', csrfToken })
        void refreshMemoryCandidates()
        void refreshKnowledgeStatus()
        void refreshToolApprovals()
        void refreshVoiceProfiles()
      } else {
        setSession({ kind: 'anonymous' })
      }
    } catch {
      setSession({ kind: 'error' })
    }
  }

  async function refreshAudioInputs() {
    try {
      setAudioInputs(await listAudioInputs())
    } catch {
      setAudioInputs([])
    }
  }

  useEffect(() => {
    void refreshSession()
    void refreshAudioInputs()
  }, [])

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSession({ kind: 'signing-in' })
    try {
      const csrfToken = (await login(password)).csrf_token
      setSession({ kind: 'authenticated', csrfToken })
      void refreshMemoryCandidates()
      void refreshKnowledgeStatus()
      void refreshToolApprovals()
      void refreshVoiceProfiles()
      setPassword('')
    } catch {
      setSession({ kind: 'anonymous' })
    }
  }

  async function signOut() {
    if (session.kind !== 'authenticated') return
    await disconnectVoice()
    await logout(session.csrfToken)
    setSession({ kind: 'anonymous' })
    setPendingCandidates([])
    setApprovedCandidates([])
    setKnowledgeStatus(null)
    setCandidateDrafts({})
    setToolApprovals([])
    setVoiceProfiles([])
    setSelectedVoiceProfileId(null)
  }

  async function connectVoice() {
    if (session.kind !== 'authenticated') return
    setVoiceState('connecting')
    setConversation(initialConversation)
    try {
      const client = new VoiceClient(
        session.csrfToken,
        (event) => {
          if (voiceClient.current === client) handleVoiceEvent(event)
        },
        audioDeviceId || undefined,
        () => handleVoiceDisconnect(client),
        (energy) => avatarEnergySink.current(energy),
      )
      voiceClient.current = client
      await client.connect()
      inputReadyTimer.current = window.setTimeout(() => void fallBackToWebSocket(session.csrfToken, client), 4_000)
      setVoiceState((current) => current === 'ready' ? current : 'preparing')
      void refreshAudioInputs()
    } catch {
      await voiceClient.current?.close().catch(() => undefined)
      voiceClient.current = null
      setVoiceState('error')
    }
  }

  async function disconnectVoice() {
    clearInputReadyTimer()
    await voiceClient.current?.close().catch(() => undefined)
    voiceClient.current = null
    setVoiceState('idle')
    setConversation(initialConversation)
  }

  function handleVoiceDisconnect(client: VoiceConnection) {
    if (voiceClient.current !== client) return
    voiceClient.current = null
    setVoiceState('disconnected')
    setConversation(initialConversation)
    void client.close().catch(() => undefined)
  }

  function handleVoiceEvent(event: VoiceEvent) {
    if (event.type === 'voice_input_ready') clearInputReadyTimer()
    if (event.type === 'memory_candidates_ready') {
      void refreshMemoryCandidates()
      return
    }
    if (event.type === 'voice_ready' || event.type === 'voice_input_ready') {
      setVoiceState((current) => current === 'idle' || current === 'error' ? current : 'ready')
      return
    }
    if (event.type === 'latency') {
      document.dispatchEvent(
        new CustomEvent('arch:latency', {
          detail: { stage: event.stage, elapsed_ms: event.elapsed_ms },
        }),
      )
    }
    setConversation((current) => {
      if (event.type === 'latency') {
        const sample = { stage: event.stage, elapsedMs: event.elapsed_ms }
        const latency = [...current.latency, sample]
        return { ...current, latency: latency.slice(-latencyHistoryLimit) }
      }
      if (event.type === 'voice_state') return { ...current, activity: event.state }
      if (event.role === 'user') {
        return { ...current, activity: event.state, userText: event.text }
      }
      if (event.phase === 'start') return { ...current, activity: event.state, assistantText: '' }
      if (event.phase === 'delta') {
        return { ...current, activity: event.state, assistantText: current.assistantText + event.text }
      }
      return { ...current, activity: event.state }
    })
  }

  function clearInputReadyTimer() {
    if (inputReadyTimer.current !== null) window.clearTimeout(inputReadyTimer.current)
    inputReadyTimer.current = null
  }

  async function fallBackToWebSocket(csrfToken: string, webRtcClient: VoiceClient) {
    if (voiceClient.current !== webRtcClient) return
    await webRtcClient.close().catch(() => undefined)
    if (voiceClient.current !== webRtcClient) return
    setVoiceState('preparing')
    const client = new WebSocketVoiceClient(
      csrfToken,
      (event) => { if (voiceClient.current === client) handleVoiceEvent(event) },
      audioDeviceId || undefined,
      () => handleVoiceDisconnect(client),
      (energy) => avatarEnergySink.current(energy),
    )
    voiceClient.current = client
    try {
      await client.connect()
    } catch {
      if (voiceClient.current === client) {
        voiceClient.current = null
        setVoiceState('error')
      }
      await client.close().catch(() => undefined)
    }
  }

  async function updateCandidate(candidateId: string, update: { content?: string; action?: 'approved' | 'rejected' | 'deleted' }) {
    if (session.kind !== 'authenticated') return
    setCandidateActionId(candidateId)
    try {
      await updateMemoryCandidate(session.csrfToken, candidateId, update)
      await refreshMemoryCandidates()
    } finally {
      setCandidateActionId(null)
    }
  }

  async function decideTool(approvalId: string, action: 'approved' | 'rejected') {
    if (session.kind !== 'authenticated') return
    setToolActionId(approvalId)
    try {
      await decideToolApproval(session.csrfToken, approvalId, action)
      await refreshToolApprovals()
    } finally {
      setToolActionId(null)
    }
  }

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (session.kind !== 'authenticated' || profileReference === null) return
    setProfileBusy(true)
    setProfileError(false)
    try {
      const referenceWavBase64 = bytesToBase64(await profileReference.arrayBuffer())
      await createVoiceProfile(session.csrfToken, {
        name: profileName,
        transcript: profileTranscript,
        referenceWavBase64,
        ownershipConfirmed: profileOwnershipConfirmed,
      })
      setProfileName('')
      setProfileTranscript('')
      setProfileReference(null)
      setProfileOwnershipConfirmed(false)
      await refreshVoiceProfiles()
    } catch {
      setProfileError(true)
    } finally {
      setProfileBusy(false)
    }
  }

  async function chooseProfile(profileId: string | null) {
    if (session.kind !== 'authenticated') return
    setProfileBusy(true)
    setProfileError(false)
    try {
      const profiles = await selectVoiceProfile(session.csrfToken, profileId)
      setVoiceProfiles(profiles.profiles)
      setSelectedVoiceProfileId(profiles.selected_id)
    } catch {
      setProfileError(true)
    } finally {
      setProfileBusy(false)
    }
  }

  async function removeProfile(profileId: string) {
    if (session.kind !== 'authenticated') return
    setProfileBusy(true)
    setProfileError(false)
    try {
      await deleteVoiceProfile(session.csrfToken, profileId)
      await refreshVoiceProfiles()
    } catch {
      setProfileError(true)
    } finally {
      setProfileBusy(false)
    }
  }

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <span className="product-name">A.R.C.H.</span>
        <span className={`connection-state connection-state--${session.kind}`} aria-live="polite">
          {session.kind === 'authenticated' ? '已登录' : '未登录'}
        </span>
      </header>
      {session.kind === 'authenticated' ? (
        <section className="setup-panel" aria-labelledby="voice-title">
          <p className="eyebrow">语音会话</p>
          <h1 id="voice-title">{voiceHeading(voiceState, conversation.activity)}</h1>
          {voiceState === 'error' ? <p>无法建立语音连接。</p> : null}
          {voiceState === 'disconnected' ? <p>媒体连接已断开，请重新连接。</p> : null}
          {voiceState === 'preparing' || voiceState === 'ready' ? (
            <div className="transcript" aria-live="polite">
              <p><span>你</span>{conversation.userText || '...'}</p>
              <p><span>助手</span>{conversation.assistantText || '...'}</p>
              {conversation.latency.map((sample, index) => (
                <p
                  className="latency"
                  data-elapsed-ms={sample.elapsedMs}
                  data-latency-stage={sample.stage}
                  key={`${sample.stage}-${sample.elapsedMs}-${index}`}
                >
                  <span>{latencyLabels[sample.stage]}</span>{sample.elapsedMs} ms
                </p>
              ))}
            </div>
          ) : null}
          <AvatarPreview setEnergySink={setAvatarEnergySink} />
          <section className="memory-review" aria-labelledby="memory-title">
            <h2 id="memory-title">待确认记忆</h2>
            {pendingCandidates.length ? (
              <div className="memory-candidate-list">
                {pendingCandidates.map((candidate) => {
                  const busy = candidateActionId === candidate.id
                  const draft = candidateDrafts[candidate.id] ?? candidate.content
                  const changed = draft.trim() !== candidate.content
                  return (
                    <div key={candidate.id} className="memory-candidate">
                      <p className="memory-source">来源消息 #{candidate.source_message_id}</p>
                      <textarea
                        aria-label="候选记忆"
                        value={draft}
                        onChange={(event) => setCandidateDrafts((current) => ({ ...current, [candidate.id]: event.target.value }))}
                        disabled={busy}
                      />
                      <div className="candidate-actions">
                        <button type="button" className="secondary-button" disabled={busy || !changed} onClick={() => void updateCandidate(candidate.id, { content: draft })}>保存</button>
                        <button type="button" disabled={busy} onClick={() => void updateCandidate(candidate.id, { content: changed ? draft : undefined, action: 'approved' })}>批准</button>
                        <button type="button" className="secondary-button" disabled={busy} onClick={() => void updateCandidate(candidate.id, { action: 'rejected' })}>拒绝</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : <p className="memory-empty">暂无待确认项目。</p>}
          </section>
          <section className="memory-review memory-review--saved" aria-labelledby="approved-memory-title">
            <h2 id="approved-memory-title">已保存记忆</h2>
            {approvedCandidates.length ? (
              <div className="memory-candidate-list">
                {approvedCandidates.map((candidate) => {
                  const busy = candidateActionId === candidate.id
                  return (
                    <div key={candidate.id} className="memory-candidate">
                      <p className="memory-source">来源消息 #{candidate.source_message_id}</p>
                      <p className="memory-approved-content">{candidate.content}</p>
                      <div className="candidate-actions">
                        <button type="button" className="secondary-button" disabled={busy} onClick={() => void updateCandidate(candidate.id, { action: 'deleted' })}>删除</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : <p className="memory-empty">暂无已保存项目。</p>}
          </section>
          <section className="knowledge-status" aria-labelledby="knowledge-status-title">
            <h2 id="knowledge-status-title">本地知识库</h2>
            {knowledgeStatus === null ? (
              <p>暂时无法读取索引状态。</p>
            ) : knowledgeStatus.source_count === 0 ? (
              <p>尚未索引文件。</p>
            ) : (
              <p>已索引 {knowledgeStatus.source_count} 个文件{knowledgeStatus.indexed_at ? `，最近更新于 ${formatTimestamp(knowledgeStatus.indexed_at)}` : ''}。</p>
            )}
            <button type="button" className="secondary-button" onClick={() => void refreshKnowledgeStatus()}>
              刷新状态
            </button>
            <button type="button" className="primary-button" disabled={knowledgeIndexing} onClick={() => void rebuildKnowledgeIndexFromBrowser()}>
              {knowledgeIndexing ? '正在建立索引' : '建立索引'}
            </button>
            {knowledgeIndexMessage ? <p role="status">{knowledgeIndexMessage}</p> : null}
          </section>
          <section className="tool-review" aria-labelledby="tool-review-title">
            <h2 id="tool-review-title">待确认工具操作</h2>
            {toolApprovals.length ? (
              <div className="tool-approval-list">
                {toolApprovals.map((approval) => {
                  const busy = toolActionId === approval.id
                  return <div key={approval.id} className="tool-approval">
                    <code>{approval.argv_json}</code>
                    <p>{approval.reason}</p>
                    <p className="tool-detail">工作目录：{approval.working_directory}</p>
                    <p className="tool-detail">将在 {formatTimestamp(approval.expires_at)} 过期</p>
                    <div className="candidate-actions">
                      <button type="button" disabled={busy} onClick={() => void decideTool(approval.id, 'approved')}>批准并执行</button>
                      <button type="button" className="secondary-button" disabled={busy} onClick={() => void decideTool(approval.id, 'rejected')}>拒绝</button>
                    </div>
                  </div>
                })}
              </div>
            ) : <p className="memory-empty">暂无待确认工具操作。</p>}
            <button type="button" className="secondary-button" onClick={() => void refreshToolApprovals()}>刷新操作</button>
          </section>
          <section className="voice-profiles" aria-labelledby="voice-profiles-title">
            <h2 id="voice-profiles-title">克隆声音参考</h2>
            <p>当前会话仍使用 Kokoro 基线。保存的参考仅在 Qwen 克隆运行时安装并完成设备验收后启用。</p>
            {profileError ? <p className="form-error">无法保存或更新声音参考。请确认 WAV 格式、时长和授权确认。</p> : null}
            <div className="voice-profile-list">
              <button type="button" className="secondary-button" disabled={profileBusy || selectedVoiceProfileId === null} onClick={() => void chooseProfile(null)}>使用 Kokoro 基线</button>
              {voiceProfiles.map((profile) => <div className="voice-profile" key={profile.id}>
                <p><strong>{profile.name}</strong> {profile.duration_ms} ms / {profile.sample_rate} Hz</p>
                <p>{profile.transcript}</p>
                <div className="candidate-actions">
                  <button type="button" className="secondary-button" disabled={profileBusy || selectedVoiceProfileId === profile.id} onClick={() => void chooseProfile(profile.id)}>{selectedVoiceProfileId === profile.id ? '已选作克隆参考' : '选择克隆参考'}</button>
                  <button type="button" className="secondary-button" disabled={profileBusy} onClick={() => void removeProfile(profile.id)}>删除</button>
                </div>
              </div>)}
            </div>
            <form className="voice-profile-form" onSubmit={createProfile}>
              <label htmlFor="profile-name">名称</label>
              <input id="profile-name" value={profileName} onChange={(event) => setProfileName(event.target.value)} required maxLength={80} />
              <label htmlFor="profile-transcript">参考音频文本</label>
              <textarea id="profile-transcript" value={profileTranscript} onChange={(event) => setProfileTranscript(event.target.value)} required maxLength={1000} />
              <label htmlFor="profile-reference">参考 WAV</label>
              <input id="profile-reference" type="file" accept="audio/wav,.wav" required onChange={(event) => setProfileReference(event.target.files?.[0] ?? null)} />
              <label className="consent-row"><input type="checkbox" checked={profileOwnershipConfirmed} onChange={(event) => setProfileOwnershipConfirmed(event.target.checked)} required />我确认有权使用此参考声音。</label>
              <button type="submit" disabled={profileBusy || profileReference === null}>{profileBusy ? '正在保存' : '保存参考'}</button>
            </form>
          </section>
          <div className="audio-input-row">
            <label htmlFor="audio-input">麦克风</label>
            <select
              id="audio-input"
              value={audioDeviceId}
              onChange={(event) => setAudioDeviceId(event.target.value)}
              disabled={voiceState === 'connecting' || voiceState === 'preparing' || voiceState === 'ready'}
            >
              <option value="">系统默认</option>
              {audioInputs.map((input) => <option key={input.deviceId} value={input.deviceId}>{input.label}</option>)}
            </select>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void refreshAudioInputs()}
              disabled={voiceState === 'connecting' || voiceState === 'preparing' || voiceState === 'ready'}
            >
              刷新
            </button>
          </div>
          <div className="action-row">
            {voiceState === 'preparing' || voiceState === 'ready' ? (
              <button type="button" className="secondary-button" onClick={() => void disconnectVoice()}>
                断开
              </button>
            ) : (
              <button type="button" onClick={() => void connectVoice()} disabled={voiceState === 'connecting'}>
                {voiceState === 'connecting' ? '正在连接' : '连接语音'}
              </button>
            )}
            <button type="button" className="secondary-button" onClick={() => void signOut()}>
              退出登录
            </button>
          </div>
        </section>
      ) : (
        <section className="setup-panel" aria-labelledby="login-title">
          <p className="eyebrow">本机管理员</p>
          <h1 id="login-title">登录</h1>
          {session.kind === 'unconfigured' ? <p>管理员密码尚未初始化。</p> : null}
          {session.kind === 'error' ? <p>无法连接后端。</p> : null}
          {session.kind !== 'unconfigured' && session.kind !== 'loading' && session.kind !== 'error' ? (
            <form onSubmit={submitLogin} className="login-form">
              <label htmlFor="password">密码</label>
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button type="submit" disabled={session.kind === 'signing-in'}>
                登录
              </button>
            </form>
          ) : null}
        </section>
      )}
    </main>
  )
}

function formatTimestamp(value: string): string {
  const timestamp = new Date(value)
  if (Number.isNaN(timestamp.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(timestamp)
}

function bytesToBase64(payload: ArrayBuffer): string {
  const bytes = new Uint8Array(payload)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

function voiceHeading(
  voiceState: 'idle' | 'connecting' | 'preparing' | 'ready' | 'disconnected' | 'error',
  activity: VoiceActivity,
): string {
  if (voiceState === 'connecting') return '正在连接'
  if (voiceState === 'preparing') return '正在准备本地语音'
  if (voiceState === 'ready') return activity === 'listening' ? '可以说话' : activityLabels[activity]
  if (voiceState === 'disconnected') return '语音连接已断开'
  return '等待连接'
}

export default App
