import { useEffect, useRef, useState, type FormEvent } from 'react'

import { fetchAuthStatus, fetchCsrfToken, login, logout } from './auth'
import { type VoiceActivity, type VoiceEvent, VoiceClient } from './voice'
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
}

const initialConversation: ConversationState = { activity: 'listening', userText: '', assistantText: '' }

const activityLabels: Record<VoiceActivity, string> = {
  'user-speaking': '正在聆听',
  recognizing: '正在识别',
  thinking: '正在思考',
  speaking: '正在播报',
  listening: '等待说话',
}

function App() {
  const [session, setSession] = useState<SessionState>({ kind: 'loading' })
  const [password, setPassword] = useState('')
  const [voiceState, setVoiceState] = useState<'idle' | 'connecting' | 'connected' | 'error'>('idle')
  const [conversation, setConversation] = useState<ConversationState>(initialConversation)
  const voiceClient = useRef<VoiceClient | null>(null)

  async function refreshSession() {
    try {
      const status = await fetchAuthStatus()
      if (!status.configured) {
        setSession({ kind: 'unconfigured' })
      } else if (status.authenticated) {
        setSession({ kind: 'authenticated', csrfToken: await fetchCsrfToken() })
      } else {
        setSession({ kind: 'anonymous' })
      }
    } catch {
      setSession({ kind: 'error' })
    }
  }

  useEffect(() => {
    void refreshSession()
  }, [])

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSession({ kind: 'signing-in' })
    try {
      setSession({ kind: 'authenticated', csrfToken: (await login(password)).csrf_token })
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
  }

  async function connectVoice() {
    if (session.kind !== 'authenticated') return
    setVoiceState('connecting')
    setConversation(initialConversation)
    try {
      const client = new VoiceClient(session.csrfToken, handleVoiceEvent)
      voiceClient.current = client
      await client.connect()
      setVoiceState('connected')
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
            </div>
          ) : null}
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
