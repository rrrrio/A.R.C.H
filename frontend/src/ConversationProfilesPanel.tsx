import { useEffect, useState, type FormEvent, type UIEvent } from 'react'

import {
  createConversationProfile,
  deleteConversationProfile,
  fetchConversationProfiles,
  fetchActiveGreetingPolicy,
  replaceConversationProfile,
  replaceActiveGreetingPolicy,
  type ActiveGreetingPolicy,
  selectConversationProfile,
  type ConversationExample,
  type ConversationProfile,
  type ConversationProfileInput,
} from './conversationProfiles'

const emptyInput: ConversationProfileInput = {
  name: '', enabled: true, persona: '', speaking_style: '', examples: [], social_temperature: 0.65,
}
const defaultGreetingPolicy: ActiveGreetingPolicy = { enabled: false, minimum_idle_minutes: 10 }

export function ConversationProfilesPanel({ csrfToken }: { csrfToken: string }) {
  const [profiles, setProfiles] = useState<ConversationProfile[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [draft, setDraft] = useState<ConversationProfileInput>(emptyInput)
  const [busy, setBusy] = useState(false)
  const [greetingPolicy, setGreetingPolicy] = useState<ActiveGreetingPolicy>(defaultGreetingPolicy)
  const [error, setError] = useState<string | null>(null)

  async function refresh() {
    try {
      const result = await fetchConversationProfiles()
      setProfiles(result.profiles)
      setSelectedId(result.selected_id)
      setError(null)
    } catch {
      setProfiles([])
      setSelectedId(null)
      setError('无法读取对话人物卡。请检查本地服务后刷新。')
    }
  }

  useEffect(() => {
    const request = window.setTimeout(() => { void refresh() }, 0)
    return () => window.clearTimeout(request)
  }, [])

  function edit(profile: ConversationProfile) {
    setEditingId(profile.id)
    setDraft({ name: profile.name, enabled: profile.enabled, persona: profile.persona, speaking_style: profile.speaking_style, examples: profile.examples, social_temperature: profile.social_temperature })
    setError(null)
    void fetchActiveGreetingPolicy(profile.id).then(setGreetingPolicy).catch(() => setGreetingPolicy(defaultGreetingPolicy))
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (editingId === null) await createConversationProfile(csrfToken, draft)
      else {
        await replaceConversationProfile(csrfToken, editingId, draft)
        await replaceActiveGreetingPolicy(csrfToken, editingId, greetingPolicy)
      }
      setEditingId(null)
      setDraft(emptyInput)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法保存对话人物卡。')
    } finally { setBusy(false) }
  }

  async function select(profileId: string | null) {
    setBusy(true)
    setError(null)
    try {
      const result = await selectConversationProfile(csrfToken, profileId)
      setProfiles(result.profiles)
      setSelectedId(result.selected_id)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法选择对话人物卡。')
    } finally { setBusy(false) }
  }

  async function remove(profileId: string) {
    setBusy(true)
    setError(null)
    try {
      await deleteConversationProfile(csrfToken, profileId)
      if (editingId === profileId) { setEditingId(null); setDraft(emptyInput) }
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '无法删除对话人物卡。')
    } finally { setBusy(false) }
  }

  function updateExample(index: number, field: keyof ConversationExample, value: string) {
    setDraft((current) => ({ ...current, examples: current.examples.map((example, currentIndex) => currentIndex === index ? { ...example, [field]: value } : example) }))
  }

  return <div className="conversation-profiles">
    <p>人物卡只控制口吻与互动方式。它不会改变主机工具、记忆审批或本地资料的安全规则。选择和主动问候设置均在下一次连接语音时生效。</p>
    {error ? <p className="form-error" role="status">{error}</p> : null}
    <div className="conversation-profile-list roller-list" onScroll={handleConversationProfileScroll}>
      <button type="button" className="secondary-button" disabled={busy || selectedId === null} onClick={() => void select(null)}>使用中性助手</button>
      {profiles.map((profile) => <div className="conversation-profile" key={profile.id}>
        <p><strong>{profile.name}</strong>{profile.enabled ? ' 已启用' : ' 已停用'}</p>
        <p>{profile.speaking_style}</p>
        <div className="candidate-actions">
          <button type="button" className="secondary-button" disabled={busy || selectedId === profile.id} onClick={() => void select(profile.id)}>{selectedId === profile.id ? '下次连接将使用' : '下次连接使用'}</button>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => edit(profile)}>编辑</button>
          <button type="button" className="secondary-button" disabled={busy} onClick={() => void remove(profile.id)}>删除</button>
        </div>
      </div>)}
    </div>
    <form className="conversation-profile-form" onSubmit={save}>
      <h3>{editingId === null ? '新建人物卡' : '编辑人物卡'}</h3>
      <label htmlFor="conversation-name">名称</label>
      <input id="conversation-name" value={draft.name} maxLength={80} required disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} />
      <label htmlFor="conversation-persona">互动方式</label>
      <textarea id="conversation-persona" value={draft.persona} maxLength={2000} required disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, persona: event.target.value }))} />
      <label htmlFor="conversation-style">说话口吻</label>
      <textarea id="conversation-style" value={draft.speaking_style} maxLength={600} required disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, speaking_style: event.target.value }))} />
      <label htmlFor="conversation-creativity">聊天变化</label>
      <select id="conversation-creativity" value={draft.social_temperature} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, social_temperature: Number(event.target.value) }))}>
        <option value={0.35}>克制</option><option value={0.5}>平衡</option><option value={0.65}>自然</option><option value={0.8}>活跃</option>
      </select>
      <label className="consent-row"><input type="checkbox" checked={draft.enabled} disabled={busy} onChange={(event) => setDraft((current) => ({ ...current, enabled: event.target.checked }))} />在下一次连接时启用此人物卡</label>
      {editingId !== null ? <fieldset disabled={busy}><legend>主动问候</legend>
        <label className="consent-row"><input type="checkbox" checked={greetingPolicy.enabled} onChange={(event) => setGreetingPolicy((current) => ({ ...current, enabled: event.target.checked }))} />在连接空闲时允许主动问候</label>
        <label htmlFor="greeting-idle">连续空闲分钟</label><input id="greeting-idle" type="number" min={5} max={60} value={greetingPolicy.minimum_idle_minutes} onChange={(event) => setGreetingPolicy((current) => ({ ...current, minimum_idle_minutes: Number(event.target.value) }))} />
      </fieldset> : null}
      {draft.examples.map((example, index) => <div className="conversation-example" key={index}>
        <label htmlFor={`conversation-example-user-${index}`}>示例用户话语</label>
        <input id={`conversation-example-user-${index}`} value={example.user} maxLength={400} required disabled={busy} onChange={(event) => updateExample(index, 'user', event.target.value)} />
        <label htmlFor={`conversation-example-assistant-${index}`}>示例回答</label>
        <textarea id={`conversation-example-assistant-${index}`} value={example.assistant} maxLength={400} required disabled={busy} onChange={(event) => updateExample(index, 'assistant', event.target.value)} />
        <button type="button" className="secondary-button" disabled={busy} onClick={() => setDraft((current) => ({ ...current, examples: current.examples.filter((_, currentIndex) => currentIndex !== index) }))}>删除示例</button>
      </div>)}
      {draft.examples.length < 6 ? <button type="button" className="secondary-button" disabled={busy} onClick={() => setDraft((current) => ({ ...current, examples: [...current.examples, { user: '', assistant: '' }] }))}>添加示例</button> : null}
      <div className="candidate-actions"><button type="submit" disabled={busy}>{busy ? '正在保存' : editingId === null ? '保存人物卡' : '保存修改'}</button>{editingId !== null ? <button type="button" className="secondary-button" disabled={busy} onClick={() => { setEditingId(null); setDraft(emptyInput) }}>取消编辑</button> : null}</div>
    </form>
  </div>
}

function handleConversationProfileScroll(event: UIEvent<HTMLDivElement>) {
  const element = event.currentTarget
  element.dataset.scrollTop = String(element.scrollTop > 0)
}
