import { useEffect, useRef, useState, type FormEvent } from 'react'

import { AvatarPreview } from './AvatarPreview'
import { AvatarVideoPreview } from './AvatarVideoPreview'
import { fetchAuthStatus, fetchCsrfToken, hasCurrentAuthContract, login, logout, reissueTerminalLease, setTerminalProfile } from './auth'
import { fetchKnowledgeStatus, rebuildKnowledgeIndex, type KnowledgeStatus } from './knowledge'
import { fetchMemoryCandidates, type MemoryCandidate, updateMemoryCandidate } from './memory'
import { decideToolApproval, fetchTerminalGrants, fetchToolApprovals, revokeTerminalGrant, type TerminalGrant, type ToolApproval } from './tools'
import { type LatencyStage, type VoiceActivity } from './voice'
import { useVoiceSession } from './useVoiceSession'
import { createVoiceProfile, deleteVoiceProfile, fetchVoiceProfiles, handleReferenceFileSelection, restartVoiceSessionForProfileSelection, selectVoiceProfile, transcribeReferenceAudio, type VoiceProfile, validateReferenceAudio } from './voices'
import './App.css'

type SessionState =
  | { kind: 'loading' }
  | { kind: 'unconfigured' }
  | { kind: 'anonymous' }
  | { kind: 'signing-in' }
  | { kind: 'authenticated'; csrfToken: string; terminalLeaseExpiresAt: string | null; terminalLeaseScopes: string[]; terminalProfile: 'observe' | 'assist' | 'operator' }
  | { kind: 'error' }

type WorkspaceSection = 'memory' | 'knowledge' | 'approvals' | 'voices' | 'agent' | 'avatars'

const workspaceSections: { id: WorkspaceSection; label: string; description: string }[] = [
  { id: 'memory', label: '记忆', description: '审阅长期偏好与事实' },
  { id: 'knowledge', label: '知识库', description: '管理本地资料索引' },
  { id: 'approvals', label: '审批', description: '确认待执行的主机操作' },
  { id: 'voices', label: '声音', description: '选择和管理本地声音参考' },
  { id: 'agent', label: '主机 Agent', description: '管理权限档位和授权' },
  { id: 'avatars', label: '数字人', description: '管理浏览器和视频呈现' },
]

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
  const [leasePassword, setLeasePassword] = useState('')
  const [leaseBusy, setLeaseBusy] = useState(false)
  const [leaseError, setLeaseError] = useState(false)
  const [pendingCandidates, setPendingCandidates] = useState<MemoryCandidate[]>([])
  const [approvedCandidates, setApprovedCandidates] = useState<MemoryCandidate[]>([])
  const [knowledgeStatus, setKnowledgeStatus] = useState<KnowledgeStatus | null>(null)
  const [knowledgeIndexing, setKnowledgeIndexing] = useState(false)
  const [knowledgeIndexMessage, setKnowledgeIndexMessage] = useState<string | null>(null)
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, string>>({})
  const [candidateActionId, setCandidateActionId] = useState<string | null>(null)
  const [toolApprovals, setToolApprovals] = useState<ToolApproval[]>([])
  const [terminalGrants, setTerminalGrants] = useState<TerminalGrant[]>([])
  const [toolActionId, setToolActionId] = useState<string | null>(null)
  const [voiceProfiles, setVoiceProfiles] = useState<VoiceProfile[]>([])
  const [selectedVoiceProfileId, setSelectedVoiceProfileId] = useState<string | null>(null)
  const [profileName, setProfileName] = useState('')
  const [profileTranscript, setProfileTranscript] = useState('')
  const [profileReference, setProfileReference] = useState<File | null>(null)
  const [profileOwnershipConfirmed, setProfileOwnershipConfirmed] = useState(false)
  const [profileBusy, setProfileBusy] = useState(false)
  const [profileTranscribing, setProfileTranscribing] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)
  const [activeWorkspaceSection, setActiveWorkspaceSection] = useState<WorkspaceSection>('memory')
  const profileTranscriptionRequest = useRef(0)

  const {
    audioDeviceId,
    audioInputs,
    connectVoice,
    conversation,
    disconnectVoice,
    refreshAudioInputs,
    setAudioDeviceId,
    setAvatarEnergySink,
    voiceState,
  } = useVoiceSession({
    csrfToken: session.kind === 'authenticated' ? session.csrfToken : null,
    onMemoryCandidatesReady: () => void refreshMemoryCandidates(),
    onToolApprovalsReady: () => void refreshToolApprovals(),
  })

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

  async function refreshTerminalGrants() {
    try {
      setTerminalGrants(await fetchTerminalGrants())
    } catch {
      setTerminalGrants([])
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
      } else if (status.authenticated && hasCurrentAuthContract(status)) {
        const csrfToken = await fetchCsrfToken()
        setSession({
          kind: 'authenticated',
          csrfToken,
          terminalLeaseExpiresAt: status.terminal_lease_expires_at ?? null,
          terminalLeaseScopes: status.terminal_lease_scopes ?? [],
          terminalProfile: status.terminal_profile ?? 'assist',
        })
        void refreshMemoryCandidates()
        void refreshKnowledgeStatus()
        void refreshToolApprovals()
        void refreshTerminalGrants()
        void refreshVoiceProfiles()
      } else if (status.authenticated) {
        setSession({ kind: 'error' })
      } else {
        setSession({ kind: 'anonymous' })
      }
    } catch {
      setSession({ kind: 'error' })
    }
  }

  useEffect(() => {
    void refreshSession()
    void refreshAudioInputs()
  }, [refreshAudioInputs])

  async function submitLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSession({ kind: 'signing-in' })
    try {
      const csrfToken = (await login(password)).csrf_token
      const status = await fetchAuthStatus()
      if (!hasCurrentAuthContract(status)) throw new Error('The backend session was not established.')
      setSession({
        kind: 'authenticated',
        csrfToken,
        terminalLeaseExpiresAt: status.terminal_lease_expires_at ?? null,
        terminalLeaseScopes: status.terminal_lease_scopes ?? [],
        terminalProfile: status.terminal_profile ?? 'assist',
      })
      void refreshMemoryCandidates()
      void refreshKnowledgeStatus()
      void refreshToolApprovals()
      void refreshTerminalGrants()
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
    setTerminalGrants([])
    setVoiceProfiles([])
    setSelectedVoiceProfileId(null)
    setLeasePassword('')
    setLeaseError(false)
  }

  async function renewTerminalLease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (session.kind !== 'authenticated' || leaseBusy) return
    setLeaseBusy(true)
    setLeaseError(false)
    try {
      const result = await reissueTerminalLease(session.csrfToken, leasePassword)
      setSession({
        ...session,
        terminalLeaseExpiresAt: result.terminal_lease_expires_at,
        terminalLeaseScopes: result.terminal_lease_scopes,
      })
      setLeasePassword('')
    } catch {
      setLeaseError(true)
    } finally {
      setLeaseBusy(false)
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
      await refreshTerminalGrants()
    } finally {
      setToolActionId(null)
    }
  }

  async function updateTerminalProfile(profile: 'observe' | 'assist' | 'operator') {
    if (session.kind !== 'authenticated') return
    const status = await setTerminalProfile(session.csrfToken, profile)
    setSession({
      ...session,
      terminalLeaseExpiresAt: status.terminal_lease_expires_at ?? null,
      terminalLeaseScopes: status.terminal_lease_scopes ?? [],
      terminalProfile: status.terminal_profile ?? profile,
    })
  }

  async function revokeGrant(grantId: string) {
    if (session.kind !== 'authenticated') return
    await revokeTerminalGrant(session.csrfToken, grantId)
    await refreshTerminalGrants()
  }

  async function chooseProfileReference(file: File | null) {
    const requestId = profileTranscriptionRequest.current + 1
    profileTranscriptionRequest.current = requestId
    setProfileReference(file)
    setProfileTranscript('')
    setProfileError(null)
    if (file === null || session.kind !== 'authenticated') {
      setProfileTranscribing(false)
      return
    }
    setProfileTranscribing(true)
    try {
      await validateReferenceAudio(file)
      const transcript = await transcribeReferenceAudio(
        session.csrfToken,
        bytesToBase64(await file.arrayBuffer()),
      )
      if (profileTranscriptionRequest.current === requestId) setProfileTranscript(transcript)
    } catch (error) {
      if (profileTranscriptionRequest.current === requestId) setProfileError(formatVoiceTranscriptionError(error))
    } finally {
      if (profileTranscriptionRequest.current === requestId) setProfileTranscribing(false)
    }
  }

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (session.kind !== 'authenticated' || profileReference === null) return
    setProfileBusy(true)
    setProfileError(null)
    try {
      await validateReferenceAudio(profileReference)
      const referenceAudioBase64 = bytesToBase64(await profileReference.arrayBuffer())
      await createVoiceProfile(session.csrfToken, {
        name: profileName,
        transcript: profileTranscript,
        referenceAudioBase64,
        ownershipConfirmed: profileOwnershipConfirmed,
      })
      setProfileName('')
      setProfileTranscript('')
      setProfileReference(null)
      setProfileOwnershipConfirmed(false)
      profileTranscriptionRequest.current += 1
      setProfileTranscribing(false)
      await refreshVoiceProfiles()
    } catch (error) {
      setProfileError(formatVoiceProfileError(error))
    } finally {
      setProfileBusy(false)
    }
  }

  async function chooseProfile(profileId: string | null) {
    if (session.kind !== 'authenticated') return
    setProfileBusy(true)
    setProfileError(null)
    try {
      const profiles = await selectVoiceProfile(session.csrfToken, profileId)
      setVoiceProfiles(profiles.profiles)
      setSelectedVoiceProfileId(profiles.selected_id)
      await restartVoiceSessionForProfileSelection(voiceState, disconnectVoice, connectVoice)
    } catch (error) {
      setProfileError(formatVoiceProfileError(error))
    } finally {
      setProfileBusy(false)
    }
  }

  async function removeProfile(profileId: string) {
    if (session.kind !== 'authenticated') return
    setProfileBusy(true)
    setProfileError(null)
    try {
      await deleteVoiceProfile(session.csrfToken, profileId)
      await refreshVoiceProfiles()
    } catch (error) {
      setProfileError(formatVoiceProfileError(error))
    } finally {
      setProfileBusy(false)
    }
  }

  return (
    <main className="workspace-shell">
      <header className="topbar">
        <span className="product-name">A.R.C.H.</span>
        <div className="topbar__actions">
          <span className={`connection-state connection-state--${session.kind}`} aria-live="polite">
            {session.kind === 'authenticated' ? '已登录' : '未登录'}
          </span>
          {session.kind === 'authenticated' ? <button type="button" className="topbar__sign-out" onClick={() => void signOut()}>退出</button> : null}
        </div>
      </header>
      {session.kind === 'authenticated' ? (
        <div className="app-workspace">
          <aside className="workspace-navigation" aria-label="工作区导航">
            <div className="workspace-navigation__intro">
              <p className="eyebrow">控制中心</p>
              <p>本机处理 · 当前会话</p>
            </div>
            <nav>
              {workspaceSections.map((section) => {
                const count = section.id === 'memory' ? pendingCandidates.length : section.id === 'approvals' ? toolApprovals.length : 0
                return (
                  <button
                    type="button"
                    className={`workspace-navigation__item${activeWorkspaceSection === section.id ? ' workspace-navigation__item--active' : ''}`}
                    aria-current={activeWorkspaceSection === section.id ? 'page' : undefined}
                    onClick={() => setActiveWorkspaceSection(section.id)}
                    key={section.id}
                  >
                    <span>{section.label}</span>
                    {count ? <strong>{count}</strong> : null}
                    <small>{section.description}</small>
                  </button>
                )
              })}
            </nav>
          </aside>
          <div className="workspace-stage">
            <section className="voice-desk" aria-labelledby="voice-title">
              <div className="voice-desk__status">
                <span className={`voice-desk__signal voice-desk__signal--${voiceState}`} aria-hidden="true" />
                <div>
                  <p className="eyebrow">实时语音会话</p>
                  <h1 id="voice-title">{voiceHeading(voiceState, conversation.activity)}</h1>
                </div>
                <div className="voice-desk__actions">
                  {voiceState === 'ready' ? (
                    <button type="button" className="secondary-button" onClick={() => void disconnectVoice()}>断开</button>
                  ) : (
                    <button type="button" onClick={() => void connectVoice()} disabled={voiceState === 'connecting' || voiceState === 'preparing'}>
                      {voiceState === 'connecting' ? '正在连接' : voiceState === 'preparing' ? '正在准备' : '连接语音'}
                    </button>
                  )}
                </div>
              </div>
              <div className="audio-input-row">
                <label htmlFor="audio-input">输入设备</label>
                <select
                  id="audio-input"
                  value={audioDeviceId}
                  onChange={(event) => setAudioDeviceId(event.target.value)}
                  disabled={voiceState === 'connecting' || voiceState === 'preparing' || voiceState === 'ready'}
                >
                  <option value="">系统默认麦克风</option>
                  {audioInputs.map((input) => <option key={input.deviceId} value={input.deviceId}>{input.label}</option>)}
                </select>
                <button type="button" className="secondary-button" onClick={() => void refreshAudioInputs()} disabled={voiceState === 'connecting' || voiceState === 'preparing' || voiceState === 'ready'}>刷新</button>
              </div>
              {voiceState === 'error' ? <p className="voice-notice" role="status">无法建立语音连接。请检查本地模型和浏览器麦克风权限后重试。</p> : null}
              {voiceState === 'disconnected' ? <p className="voice-notice" role="status">媒体连接已断开，请重新连接。</p> : null}
              <div className="transcript" aria-live="polite">
                <div className="transcript__message"><span>你</span><p>{conversation.userText || '...'}</p></div>
                <div className="transcript__message transcript__message--assistant"><span>助手</span><p>{conversation.assistantText || '...'}</p></div>
              </div>
              {conversation.latency.length ? (
                <div className="latency-strip" aria-label="本轮阶段延迟">
                  {conversation.latency.map((sample, index) => (
                    <p className="latency" data-elapsed-ms={sample.elapsedMs} data-latency-stage={sample.stage} key={`${sample.stage}-${sample.elapsedMs}-${index}`}>
                      <span>{latencyLabels[sample.stage]}</span>{sample.elapsedMs} ms
                    </p>
                  ))}
                </div>
              ) : null}
            </section>
            <div className="workspace-content">
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'memory'} aria-labelledby="memory-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">长期上下文</p><h2 id="memory-title">待确认记忆</h2></div><span className="panel-count">{pendingCandidates.length} 项待处理</span></div>
          <div className="memory-review">
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
          </div>
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
              </section>
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'knowledge'} aria-labelledby="knowledge-status-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">离线检索</p><h2 id="knowledge-status-title">本地知识库</h2></div></div>
          <div className="knowledge-status">
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
          </div>
              </section>
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'approvals'} aria-labelledby="tool-review-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">安全确认</p><h2 id="tool-review-title">待确认工具操作</h2></div><span className="panel-count">{toolApprovals.length} 项待处理</span></div>
          <div className="tool-review">
            {toolApprovals.length ? (
              <div className="tool-approval-list">
                {toolApprovals.map((approval) => {
                  const busy = toolActionId === approval.id
                  return <div key={approval.id} className="tool-approval">
                    <pre className="tool-command-preview">{approval.argv_json}</pre>
                    <p>{approval.reason}</p>
                    <p className="tool-detail">工作目录：{approval.working_directory}</p>
                    <p className="tool-detail">批准后将以当前普通用户权限执行一次。</p>
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
          </div>
              </section>
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'voices'} aria-labelledby="voice-profiles-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">输出声音</p><h2 id="voice-profiles-title">克隆声音参考</h2></div></div>
          <div className="voice-profiles">
            <p>{voiceState === 'connecting' || voiceState === 'preparing' || voiceState === 'ready' ? '选择克隆参考后，当前语音会自动重新连接并应用所选声音。' : '选择克隆参考后，会在下一次连接语音时应用所选声音。保存的参考仅在 Qwen 克隆运行时安装并完成设备验收后启用。'}</p>
            <p className="tool-detail">支持常见音频格式和多声道；需要转换的音频会由服务器转为单声道 16-bit PCM WAV。文件不超过 5 MiB，时长不超过 10 秒。</p>
            {profileTranscribing ? <p className="tool-detail" role="status">正在自动转录参考音频，完成后可以编辑文本。</p> : null}
            {profileError ? <p className="form-error">{profileError}</p> : null}
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
              <p className="tool-detail">自动转录结果会填入这里，请核对并编辑为与录音逐字一致的文本。</p>
              <textarea id="profile-transcript" value={profileTranscript} onChange={(event) => setProfileTranscript(event.target.value)} disabled={profileTranscribing || profileBusy} required maxLength={1000} />
              <label htmlFor="profile-reference">参考音频</label>
              <input id="profile-reference" type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.aac,.ogg,.opus,.webm" required onChange={(event) => { handleReferenceFileSelection(event.currentTarget, (file) => { void chooseProfileReference(file) }) }} />
              <label className="consent-row"><input type="checkbox" checked={profileOwnershipConfirmed} onChange={(event) => setProfileOwnershipConfirmed(event.target.checked)} required />我确认有权使用此参考声音。</label>
              <button type="submit" disabled={profileBusy || profileTranscribing || profileReference === null}>{profileBusy ? '正在保存' : '保存参考'}</button>
            </form>
          </div>
              </section>
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'agent'} aria-labelledby="terminal-policy-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">受控本机访问</p><h2 id="terminal-policy-title">主机 Agent 权限</h2></div></div>
                <p className="tool-detail">终端租约：{session.terminalLeaseExpiresAt ? `有效至 ${formatTimestamp(session.terminalLeaseExpiresAt)}` : '已失效'}</p>
                <form className="lease-renewal" onSubmit={renewTerminalLease}>
                  <label htmlFor="terminal-lease-password">管理员密码</label>
                  <input id="terminal-lease-password" type="password" autoComplete="current-password" value={leasePassword} onChange={(event) => setLeasePassword(event.target.value)} required disabled={leaseBusy} />
                  <button type="submit" className="secondary-button" disabled={leaseBusy}>{leaseBusy ? '正在获取' : '重新获取终端租约'}</button>
                  {leaseError ? <p className="form-error">无法获取终端租约，请检查密码并确认当前使用 HTTPS 入口。</p> : null}
                </form>
                <div className="terminal-policy">
                  <label htmlFor="terminal-profile">当前档位</label>
                  <select id="terminal-profile" value={session.terminalProfile} onChange={(event) => void updateTerminalProfile(event.target.value as 'observe' | 'assist' | 'operator')}>
                    <option value="observe">观察</option><option value="assist">协助</option><option value="operator">主机操作</option>
                  </select>
                  {terminalGrants.length ? <div className="terminal-grant-list">{terminalGrants.map((grant) => <div key={grant.id} className="terminal-grant"><code>{grant.tool_name} · {grant.access} · {grant.working_directory}</code><p>{grant.capabilities.length ? grant.capabilities.join('、') : '无额外 capability'}，有效至 {formatTimestamp(grant.expires_at)}</p><button type="button" className="secondary-button" onClick={() => void revokeGrant(grant.id)}>撤销</button></div>)}</div> : <p className="memory-empty">暂无活动终端授权。</p>}
                </div>
              </section>
              <section className="workspace-panel" hidden={activeWorkspaceSection !== 'avatars'} aria-labelledby="avatar-workspace-title">
                <div className="workspace-panel__heading"><div><p className="eyebrow">可选呈现</p><h2 id="avatar-workspace-title">数字人</h2></div></div>
                <AvatarVideoPreview csrfToken={session.csrfToken} />
                <AvatarPreview setEnergySink={setAvatarEnergySink} />
              </section>
            </div>
          </div>
        </div>
      ) : (
        <section className="setup-panel" aria-labelledby="login-title">
          <p className="eyebrow">本机管理员</p>
          <h1 id="login-title">登录</h1>
          {session.kind === 'unconfigured' ? <p>管理员密码尚未初始化。</p> : null}
          {session.kind === 'error' ? <p>无法读取登录状态或终端租约，请使用 HTTPS 入口并刷新页面。</p> : null}
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

function formatVoiceProfileError(error: unknown): string {
  const message = error instanceof Error ? error.message : ''
  const translations: Record<string, string> = {
    'Reference audio must be a valid WAV file.': '参考音频必须是有效的 WAV 文件。',
    'Reference audio must be a supported audio file.': '参考音频格式无法读取，请上传包含音轨的常见音频文件。',
    'Audio conversion requires FFmpeg.': '服务器未准备音频转换组件（FFmpeg）。',
    'Reference WAV must be at most 10 seconds.': '参考音频时长必须在 10 秒以内。',
    'Confirm that you have permission to use this reference voice.': '请确认你有权使用此参考声音。',
  }
  const detail = translations[message] ?? message
  return detail ? `无法保存或更新声音参考：${detail}` : '无法保存或更新声音参考，请检查输入后重试。'
}

function formatVoiceTranscriptionError(error: unknown): string {
  const message = error instanceof Error ? error.message : ''
  const translations: Record<string, string> = {
    'Reference audio must be a supported audio file.': '参考音频格式无法读取，请上传包含音轨的常见音频文件。',
    'Audio conversion requires FFmpeg.': '服务器未准备音频转换组件（FFmpeg）。',
    'Local ASR model is not ready.': '本地语音转录模型尚未准备，请先运行 ASR 准备流程。',
    'Local ASR model is not available.': '本地语音转录模型当前不可用。',
    'Local reference transcription failed.': '本地语音转录失败。',
    'No speech was detected in the reference audio.': '没有检测到可转录的人声，请手动填写文本。',
  }
  const detail = translations[message] ?? message
  return detail ? `无法自动填写参考音频文本：${detail}` : '无法自动填写参考音频文本，请手动填写后重试。'
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
