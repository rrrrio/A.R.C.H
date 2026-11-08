import { useEffect, useRef, useState, type FormEvent } from 'react'

import { fetchAuthStatus, fetchCsrfToken, login, logout } from './auth'
import { fetchMemoryCandidates, type MemoryCandidate, updateMemoryCandidate } from './memory'
import { listAudioInputs, type AudioInput, type LatencyStage, type VoiceActivity, type VoiceEvent, VoiceClient } from './voice'
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
  latency: { stage: LatencyStage; elapsedMs: number } | null
}

const initialConversation: ConversationState = { activity: 'listening', userText: '', assistantText: '', latency: null }

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
  const [voiceState, setVoiceState] = useState<'idle' | 'connecting' | 'connected' | 'error'>('idle')
  const [conversation, setConversation] = useState<ConversationState>(initialConversation)
  const [audioInputs, setAudioInputs] = useState<AudioInput[]>([])
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const [pendingCandidates, setPendingCandidates] = useState<MemoryCandidate[]>([])
  const [approvedCandidates, setApprovedCandidates] = useState<MemoryCandidate[]>([])
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, string>>({})
  const [candidateActionId, setCandidateActionId] = useState<string | null>(null)
  const voiceClient = useRef<VoiceClient | null>(null)

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

  async function refreshSession() {
    try {
      const status = await fetchAuthStatus()
      if (!status.configured) {
        setSession({ kind: 'unconfigured' })
      } else if (status.authenticated) {
        const csrfToken = await fetchCsrfToken()
        setSession({ kind: 'authenticated', csrfToken })
        void refreshMemoryCandidates()
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
    setCandidateDrafts({})
  }

  async function connectVoice() {
    if (session.kind !== 'authenticated') return
    setVoiceState('connecting')
    setConversation(initialConversation)
    try {
      const client = new VoiceClient(session.csrfToken, handleVoiceEvent, audioDeviceId || undefined)
      voiceClient.current = client
      await client.connect()
      setVoiceState('connected')
      void refreshAudioInputs()
    } catch {
      await voiceClient.current?.close().catch(() => undefined)
      voiceClient.current = null
      setVoiceState('error')
    }
  }

  async function disconnectVoice() {
    await voiceClient.current?.close().catch(() => undefined)
    voiceClient.current = null
    setVoiceState('idle')
    setConversation(initialConversation)
  }

  function handleVoiceEvent(event: VoiceEvent) {
    setConversation((current) => {
      if (event.type === 'latency') {
        return { ...current, latency: { stage: event.stage, elapsedMs: event.elapsed_ms } }
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

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <span className="product-name">Local Voice Agent</span>
        <span className={`connection-state connection-state--${session.kind}`} aria-live="polite">
          {session.kind === 'authenticated' ? '已登录' : '未登录'}
        </span>
      </header>
      {session.kind === 'authenticated' ? (
        <section className="setup-panel" aria-labelledby="voice-title">
          <p className="eyebrow">语音会话</p>
          <h1 id="voice-title">{voiceState === 'connected' ? activityLabels[conversation.activity] : '等待连接'}</h1>
          {voiceState === 'error' ? <p>无法建立语音连接。</p> : null}
          {voiceState === 'connected' ? (
            <div className="transcript" aria-live="polite">
              <p><span>你</span>{conversation.userText || '...'}</p>
              <p><span>助手</span>{conversation.assistantText || '...'}</p>
              {conversation.latency ? <p className="latency"><span>{latencyLabels[conversation.latency.stage]}</span>{conversation.latency.elapsedMs} ms</p> : null}
            </div>
          ) : null}
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
          <div className="audio-input-row">
            <label htmlFor="audio-input">麦克风</label>
            <select
              id="audio-input"
              value={audioDeviceId}
              onChange={(event) => setAudioDeviceId(event.target.value)}
              disabled={voiceState === 'connecting' || voiceState === 'connected'}
            >
              <option value="">系统默认</option>
              {audioInputs.map((input) => <option key={input.deviceId} value={input.deviceId}>{input.label}</option>)}
            </select>
            <button
              type="button"
              className="secondary-button"
              onClick={() => void refreshAudioInputs()}
              disabled={voiceState === 'connecting' || voiceState === 'connected'}
            >
              刷新
            </button>
          </div>
          <div className="action-row">
            {voiceState === 'connected' ? (
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

export default App
