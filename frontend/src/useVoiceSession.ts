import { useCallback, useRef, useState } from 'react'

import {
  listAudioInputs,
  type AudioInput,
  type LatencyStage,
  type VoiceActivity,
  type VoiceConnection,
  type VoiceEvent,
  type VoiceTransport,
  VoiceClient,
  WebSocketVoiceClient,
} from './voice'

export type VoiceSessionState = 'idle' | 'connecting' | 'preparing' | 'ready' | 'disconnected' | 'error'

export type ConversationState = {
  activity: VoiceActivity
  userText: string
  assistantText: string
  latency: { stage: LatencyStage; elapsedMs: number }[]
}

type VoiceSessionOptions = {
  csrfToken: string | null
  onMemoryCandidatesReady: () => void
  onToolApprovalsReady: () => void
}

const initialConversation: ConversationState = { activity: 'listening', userText: '', assistantText: '', latency: [] }
const latencyHistoryLimit = 8

/** Owns browser media clients independently from the workspace presentation. */
export function useVoiceSession({ csrfToken, onMemoryCandidatesReady, onToolApprovalsReady }: VoiceSessionOptions) {
  const [voiceState, setVoiceState] = useState<VoiceSessionState>('idle')
  const [conversation, setConversation] = useState<ConversationState>(initialConversation)
  const [audioInputs, setAudioInputs] = useState<AudioInput[]>([])
  const [audioDeviceId, setAudioDeviceId] = useState('')
  const voiceClient = useRef<VoiceConnection | null>(null)
  const preferredVoiceTransport = useRef<VoiceTransport>('webrtc')
  const inputReadyTimer = useRef<number | null>(null)
  const avatarEnergySink = useRef<(energy: number) => void>(() => undefined)
  const avatarMediaConnected = useRef(false)

  const setAvatarEnergySink = useCallback((sink: (energy: number) => void) => {
    avatarEnergySink.current = sink
  }, [])

  const setAvatarMediaConnected = useCallback((connected: boolean) => {
    avatarMediaConnected.current = connected
    voiceClient.current?.setAssistantAudioMuted?.(connected)
  }, [])

  const refreshAudioInputs = useCallback(async () => {
    try {
      setAudioInputs(await listAudioInputs())
    } catch {
      setAudioInputs([])
    }
  }, [])

  const clearInputReadyTimer = useCallback(() => {
    if (inputReadyTimer.current !== null) window.clearTimeout(inputReadyTimer.current)
    inputReadyTimer.current = null
  }, [])

  const handleVoiceEvent = useCallback((event: VoiceEvent) => {
    if (event.type === 'voice_input_ready') clearInputReadyTimer()
    if (event.type === 'memory_candidates_ready') {
      onMemoryCandidatesReady()
      return
    }
    if (event.type === 'tool_approvals_ready') {
      onToolApprovalsReady()
      return
    }
    if (event.type === 'voice_ready' || event.type === 'voice_input_ready') {
      setVoiceState((current) => current === 'idle' || current === 'error' ? current : 'ready')
      return
    }
    if (event.type === 'latency') {
      document.dispatchEvent(new CustomEvent('arch:latency', { detail: { stage: event.stage, elapsed_ms: event.elapsed_ms } }))
    }
    setConversation((current) => {
      if (event.type === 'latency') {
        const latency = [...current.latency, { stage: event.stage, elapsedMs: event.elapsed_ms }]
        return { ...current, latency: latency.slice(-latencyHistoryLimit) }
      }
      if (event.type === 'voice_state') return { ...current, activity: event.state }
      if (event.role === 'user') return { ...current, activity: event.state, userText: event.text }
      if (event.phase === 'start') return { ...current, activity: event.state, assistantText: '' }
      if (event.phase === 'delta') return { ...current, activity: event.state, assistantText: current.assistantText + event.text }
      return { ...current, activity: event.state }
    })
  }, [clearInputReadyTimer, onMemoryCandidatesReady, onToolApprovalsReady])

  const handleVoiceDisconnect = useCallback((client: VoiceConnection) => {
    if (voiceClient.current !== client) return
    voiceClient.current = null
    setVoiceState((current) => current === 'connecting' || current === 'preparing' ? 'error' : 'disconnected')
    setConversation(initialConversation)
    void client.close().catch(() => undefined)
  }, [])

  const connectWebSocketVoice = useCallback(async (token: string) => {
    setVoiceState('preparing')
    const client = new WebSocketVoiceClient(
      token,
      (event) => { if (voiceClient.current === client) handleVoiceEvent(event) },
      audioDeviceId || undefined,
      () => handleVoiceDisconnect(client),
      (energy) => avatarEnergySink.current(energy),
    )
    voiceClient.current = client
    try {
      await client.connect()
      client.setAssistantAudioMuted?.(avatarMediaConnected.current)
      if (voiceClient.current !== client) await client.close().catch(() => undefined)
    } catch {
      if (voiceClient.current === client) {
        voiceClient.current = null
        setVoiceState('error')
      }
      await client.close().catch(() => undefined)
    }
  }, [audioDeviceId, handleVoiceDisconnect, handleVoiceEvent])

  const fallBackToWebSocket = useCallback(async (token: string, webRtcClient: VoiceClient) => {
    if (voiceClient.current !== webRtcClient) return
    await webRtcClient.close().catch(() => undefined)
    if (voiceClient.current !== webRtcClient) return
    preferredVoiceTransport.current = 'websocket'
    await connectWebSocketVoice(token)
  }, [connectWebSocketVoice])

  const connectVoice = useCallback(async () => {
    if (csrfToken === null) return
    setVoiceState('connecting')
    setConversation(initialConversation)
    if (preferredVoiceTransport.current === 'websocket') {
      await connectWebSocketVoice(csrfToken)
      return
    }
    const client = new VoiceClient(
      csrfToken,
      (event) => {
        if (voiceClient.current !== client) return
        if (event.type === 'voice_input_ready') preferredVoiceTransport.current = 'webrtc'
        handleVoiceEvent(event)
      },
      audioDeviceId || undefined,
      () => handleVoiceDisconnect(client),
      (energy) => avatarEnergySink.current(energy),
    )
    voiceClient.current = client
    try {
      await client.connect()
      client.setAssistantAudioMuted?.(avatarMediaConnected.current)
      if (voiceClient.current !== client) {
        await client.close().catch(() => undefined)
        return
      }
      inputReadyTimer.current = window.setTimeout(() => void fallBackToWebSocket(csrfToken, client), 4_000)
      setVoiceState((current) => current === 'ready' ? current : 'preparing')
      void refreshAudioInputs()
    } catch {
      if (voiceClient.current === client) {
        await client.close().catch(() => undefined)
        voiceClient.current = null
        setVoiceState('error')
      }
    }
  }, [audioDeviceId, connectWebSocketVoice, csrfToken, fallBackToWebSocket, handleVoiceDisconnect, handleVoiceEvent, refreshAudioInputs])

  const disconnectVoice = useCallback(async () => {
    clearInputReadyTimer()
    await voiceClient.current?.close().catch(() => undefined)
    voiceClient.current = null
    setVoiceState('idle')
    setConversation(initialConversation)
  }, [clearInputReadyTimer])

  return {
    audioDeviceId,
    audioInputs,
    connectVoice,
    conversation,
    disconnectVoice,
    refreshAudioInputs,
    setAudioDeviceId,
    setAvatarEnergySink,
    setAvatarMediaConnected,
    voiceState,
  }
}
