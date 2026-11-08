import { useState } from 'react'

import { fetchHealth, type HealthResponse } from './health'
import './App.css'

type ConnectionState =
  | { kind: 'idle' }
  | { kind: 'checking' }
  | { kind: 'ready'; health: HealthResponse }
  | { kind: 'error' }

function App() {
  const [connection, setConnection] = useState<ConnectionState>({ kind: 'idle' })

  async function checkBackend() {
    setConnection({ kind: 'checking' })
    try {
      setConnection({ kind: 'ready', health: await fetchHealth() })
    } catch {
      setConnection({ kind: 'error' })
    }
  }

  const detail =
    connection.kind === 'ready'
      ? `后端可用 (${connection.health.environment})`
      : connection.kind === 'error'
        ? '无法连接后端'
        : connection.kind === 'checking'
          ? '正在检查后端'
          : '尚未检查后端'

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <span className="product-name">Local Voice Agent</span>
        <span className={`connection-state connection-state--${connection.kind}`} aria-live="polite">
          {detail}
        </span>
      </header>
      <section className="setup-panel" aria-labelledby="setup-title">
        <p className="eyebrow">Milestone 0</p>
        <h1 id="setup-title">开发环境已就绪</h1>
        <p>后端健康检查是当前唯一开放的应用接口。</p>
        <button type="button" onClick={checkBackend} disabled={connection.kind === 'checking'}>
          检查后端
        </button>
      </section>
    </main>
  )
}

export default App
