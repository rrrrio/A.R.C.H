#!/usr/bin/env node

import { spawn } from 'node:child_process'

let configurationText = ''
for await (const chunk of process.stdin) configurationText += chunk
const configuration = JSON.parse(configurationText)

class CdpClient {
  constructor(socket) {
    this.socket = socket
    this.nextId = 1
    this.pending = new Map()
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (!('id' in message)) return
      const pending = this.pending.get(message.id)
      if (!pending) return
      this.pending.delete(message.id)
      if (message.error) pending.reject(new Error(message.error.message))
      else pending.resolve(message.result)
    })
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) pending.reject(new Error('avatar-browser-smoke: CDP closed'))
      this.pending.clear()
    })
  }

  command(method, params = {}) {
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`avatar-browser-smoke: CDP command timed out: ${method}`))
      }, 15_000)
      this.pending.set(id, {
        resolve: (result) => {
          clearTimeout(timeout)
          resolve(result)
        },
        reject: (error) => {
          clearTimeout(timeout)
          reject(error)
        },
      })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  async evaluate(expression) {
    const response = await this.command('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (response.exceptionDetails) {
      throw new Error(response.exceptionDetails.exception?.description ?? response.exceptionDetails.text)
    }
    return response.result.value
  }

  close() {
    this.socket.close()
  }
}

async function waitFor(description, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      if (await predicate()) return
    } catch (error) {
      lastError = error
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  const suffix = lastError instanceof Error ? `: ${lastError.message}` : ''
  throw new Error(`avatar-browser-smoke: timed out waiting for ${description}${suffix}`)
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) throw new Error(`avatar-browser-smoke: ${url} returned ${response.status}`)
  return response.json()
}

async function openSocket(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url)
    const timeout = setTimeout(() => reject(new Error('avatar-browser-smoke: CDP timed out')), 10_000)
    socket.addEventListener('open', () => {
      clearTimeout(timeout)
      resolve(socket)
    }, { once: true })
    socket.addEventListener('error', () => {
      clearTimeout(timeout)
      reject(new Error('avatar-browser-smoke: CDP failed'))
    }, { once: true })
  })
}

async function stopChrome(chrome) {
  if (chrome.exitCode !== null) return
  const exited = new Promise((resolve) => chrome.once('exit', resolve))
  try {
    globalThis.process.kill(-chrome.pid, 'SIGTERM')
  } catch (error) {
    if (error.code !== 'ESRCH') throw error
  }
  await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5_000))])
  if (chrome.exitCode === null) {
    try {
      globalThis.process.kill(-chrome.pid, 'SIGKILL')
    } catch (error) {
      if (error.code !== 'ESRCH') throw error
    }
  }
}

const chromeArguments = [
  '--headless=new',
  '--no-first-run',
  '--no-default-browser-check',
  '--no-proxy-server',
  '--ignore-certificate-errors',
  '--autoplay-policy=no-user-gesture-required',
  '--use-fake-ui-for-media-stream',
  '--use-fake-device-for-media-stream',
  '--disable-features=WebRtcHideLocalIpsWithMdns',
  ...(configuration.windowSize ? [`--window-size=${configuration.windowSize}`] : []),
  ...(configuration.audio ? [`--use-file-for-fake-audio-capture=${configuration.audio}`] : []),
  ...(configuration.host ? [`--host-resolver-rules=MAP ${configuration.host} 127.0.0.1`] : []),
  `--user-data-dir=${configuration.profileDir}`,
  `--remote-debugging-port=${configuration.debugPort}`,
]

const chrome = spawn(configuration.chrome, chromeArguments, { detached: true, stdio: 'ignore' })
let client
try {
  const debuggingUrl = `http://127.0.0.1:${configuration.debugPort}`
  await waitFor('Chrome DevTools', async () => {
    try {
      await fetchJson(`${debuggingUrl}/json/version`)
      return true
    } catch {
      return false
    }
  }, 20_000)

  const target = await fetchJson(
    `${debuggingUrl}/json/new?${encodeURIComponent(configuration.url)}`,
    { method: 'PUT' },
  )
  client = new CdpClient(await openSocket(target.webSocketDebuggerUrl))
  await client.command('Runtime.enable')
  await waitFor(
    'login page',
    async () => (await client.evaluate("document.body.innerText.includes('本机管理员')")),
    configuration.timeoutSeconds * 1_000,
  )

  const password = JSON.stringify(configuration.password)
  await client.evaluate(`(() => {
    const input = document.querySelector('#password')
    const form = input?.closest('form')
    if (!input || !form) throw new Error('login controls are missing')
    const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
    if (!setValue) throw new Error('password value setter is missing')
    setValue.call(input, ${password})
    input.dispatchEvent(new Event('input', { bubbles: true }))
    form.requestSubmit()
  })()`)
  await waitFor(
    'authenticated avatar controls',
    async () => (await client.evaluate("document.body.innerText.includes('已登录') && document.querySelector('.avatar-video-control') !== null")),
    configuration.timeoutSeconds * 1_000,
  )

  await waitFor(
    'enabled avatar service',
    async () => (await client.evaluate(`(() => {
      const button = [...document.querySelectorAll('.avatar-video-control button')]
        .find((candidate) => candidate.textContent?.trim() === '启用视频')
      return Boolean(button && !button.disabled)
    })()`)),
    configuration.timeoutSeconds * 1_000,
  )
  if (configuration.enableAcceptanceRecorder) {
    await client.evaluate(`(() => {
      const input = [...document.querySelectorAll('.avatar-video-control input[type="checkbox"]')]
        .find((candidate) => candidate.parentElement?.textContent?.includes('目标设备验收'))
      if (!(input instanceof HTMLInputElement)) throw new Error('avatar acceptance control is missing')
      input.click()
      if (!input.checked) throw new Error('avatar acceptance control did not enable')
    })()`)
  }
  await client.evaluate(`(() => {
    const button = [...document.querySelectorAll('.avatar-video-control button')]
      .find((candidate) => candidate.textContent?.trim() === '启用视频')
    if (!button) throw new Error('avatar enable control is missing')
    button.click()
  })()`)
  await waitFor(
    'avatar WebRTC connection',
    async () => (await client.evaluate("document.querySelector('[data-avatar-state=\"connected\"]') !== null")),
    configuration.timeoutSeconds * 1_000,
  )
  await client.evaluate(`(() => {
    const layer = document.querySelector('.avatar-video-layer')
    if (!(layer instanceof HTMLElement)) throw new Error('avatar video layer is missing')
    if (layer.parentElement !== document.querySelector('.workspace-shell')) {
      throw new Error('avatar video layer is outside the workspace stacking context')
    }
    if (getComputedStyle(layer).pointerEvents !== 'none') {
      throw new Error('avatar video layer must not capture page controls')
    }
    if (layer.classList.contains('avatar-video-layer--live')) {
      throw new Error('avatar video layer became visible before its first decoded frame')
    }
  })()`)

  if (!configuration.injectPcm) {
    await client.evaluate(`(() => {
      const button = [...document.querySelectorAll('button')]
        .find((candidate) => candidate.textContent?.trim() === '连接语音')
      if (!button) throw new Error('voice connection control is missing')
      button.click()
    })()`)
    await waitFor(
      'voice response PCM',
      async () => (await client.evaluate(`(() => {
        const rows = [...document.querySelectorAll('.transcript p')]
        return rows.some((row) => row.textContent?.startsWith('助手') && row.textContent.trim() !== '助手...') &&
          rows.some((row) => row.textContent?.startsWith('发送首段音频'))
      })()`)),
      configuration.timeoutSeconds * 1_000,
    )
  }

  const video = await client.evaluate(`(() => new Promise((resolve, reject) => {
    const element = document.querySelector('.avatar-video-layer video')
    if (!(element instanceof HTMLVideoElement)) {
      reject(new Error('avatar video element is missing'))
      return
    }
    const requiredCallbacks = ${JSON.stringify(configuration.requiredVideoCallbacks ?? 3)}
    const verifyCadence = ${JSON.stringify(configuration.verifyVideoCadence ?? false)}
    const minimumGapMs = ${JSON.stringify(configuration.minimumFrameGapMs ?? 15)}
    const maximumGapMs = ${JSON.stringify(configuration.maximumFrameGapMs ?? 120)}
    const maximumRenderedHeight = ${JSON.stringify(configuration.maximumRenderedVideoHeight ?? null)}
    const requireSinglePeerAudio = ${JSON.stringify(configuration.requireSinglePeerAudio ?? false)}
    let callbacks = 0
    let firstTimestamp = null
    let previousTimestamp = null
    const intervals = []
    const deadline = window.setTimeout(() => reject(new Error('video frames were not decoded')), 12_000)
    const observe = () => element.requestVideoFrameCallback(() => {
      const timestamp = performance.now()
      if (firstTimestamp === null) firstTimestamp = timestamp
      if (previousTimestamp !== null) intervals.push(timestamp - previousTimestamp)
      previousTimestamp = timestamp
      callbacks += 1
      if (callbacks >= requiredCallbacks && element.videoWidth > 0 && element.videoHeight > 0) {
        window.clearTimeout(deadline)
        if (verifyCadence) {
          const elapsedMs = previousTimestamp === null || firstTimestamp === null
            ? 0
            : previousTimestamp - firstTimestamp
          const averageFps = elapsedMs > 0 ? (callbacks - 1) * 1_000 / elapsedMs : 0
          const sustainedBurst = intervals.some((interval, index) => (
            index > 0 && interval < minimumGapMs && intervals[index - 1] < minimumGapMs
          ))
          if (averageFps < 22 || averageFps > 28 || sustainedBurst || intervals.some((interval) => interval > maximumGapMs)) {
            reject(new Error('video frame cadence was unstable: ' + JSON.stringify({ averageFps, intervals })))
            return
          }
        }
        const renderedHeight = element.getBoundingClientRect().height
        if (maximumRenderedHeight !== null && renderedHeight > maximumRenderedHeight + 1) {
          reject(new Error('video was upscaled beyond its native portrait height: ' + renderedHeight))
          return
        }
        const stream = element.srcObject
        if (requireSinglePeerAudio) {
          if (!(stream instanceof MediaStream)) {
            reject(new Error('avatar media stream is missing'))
            return
          }
          const audioTracks = stream.getAudioTracks()
          const videoTracks = stream.getVideoTracks()
          if (element.muted || element.paused || audioTracks.length !== 1 || videoTracks.length !== 1) {
            reject(new Error('avatar peer does not own one unmuted audio/video playback element'))
            return
          }
          const duplicateOutputs = [...document.querySelectorAll('audio, video')].filter((candidate) => {
            if (candidate === element || candidate.muted || candidate.paused) return false
            const output = candidate.srcObject
            return output instanceof MediaStream && output.getAudioTracks().length > 0
          })
          if (duplicateOutputs.length !== 0) {
            reject(new Error('ordinary assistant audio remains active during avatar playback'))
            return
          }
        }
        resolve({
          audioTracks: stream instanceof MediaStream ? stream.getAudioTracks().length : 0,
          callbacks,
          height: element.videoHeight,
          intervals,
          renderedHeight,
          width: element.videoWidth,
        })
        return
      }
      observe()
    })
    observe()
  }))()`)

  await client.evaluate(`(() => {
    const layer = document.querySelector('.avatar-video-layer')
    const workspace = document.querySelector('.workspace-shell')
    const element = document.querySelector('.avatar-video-layer video')
    if (!(layer instanceof HTMLElement) || !(workspace instanceof HTMLElement) || !(element instanceof HTMLVideoElement)) {
      throw new Error('avatar visibility controls are missing')
    }
    if (layer.parentElement !== workspace) {
      throw new Error('avatar video layer is outside the workspace stacking context')
    }
    if (getComputedStyle(layer).opacity !== '1') {
      throw new Error('avatar video layer is not visible after frame decode')
    }
    const bounds = element.getBoundingClientRect()
    if (bounds.width <= 0 || bounds.height <= 0) {
      throw new Error('avatar video has no visible bounds')
    }
    if (getComputedStyle(layer).zIndex !== '0') {
      throw new Error('avatar video layer no longer occupies the workspace background plane')
    }
    const workspaceContent = document.querySelector('.app-workspace')
    if (!(workspaceContent instanceof HTMLElement) || getComputedStyle(workspaceContent).zIndex !== '1') {
      throw new Error('workspace controls are not positioned above the avatar video')
    }
  })()`)

  await client.evaluate(`(() => {
    const button = [...document.querySelectorAll('.avatar-video-control button')]
      .find((candidate) => candidate.textContent?.trim() === '关闭视频')
    if (!button) throw new Error('avatar close control is missing')
    button.click()
  })()`)
  await waitFor(
    'avatar session release',
    async () => (await client.evaluate(`(() => {
      const status = document.querySelector('[data-avatar-state]')
      const video = document.querySelector('.avatar-video-layer video')
      return status?.getAttribute('data-avatar-state') === 'ready' && video?.srcObject === null
    })()`)),
    configuration.timeoutSeconds * 1_000,
  )

  if (!configuration.injectPcm) {
    await client.evaluate(`(() => {
      const button = [...document.querySelectorAll('button')]
        .find((candidate) => candidate.textContent?.trim() === '断开')
      if (!button) throw new Error('voice disconnect control is missing')
      button.click()
    })()`)
    await waitFor(
      'voice disconnect',
      async () => (await client.evaluate("document.body.innerText.includes('连接语音')")),
      configuration.timeoutSeconds * 1_000,
    )
  }
  console.log(`avatar-browser-smoke: result ${JSON.stringify(video)}`)
  console.log('avatar-browser-smoke: passed')
} finally {
  if (client) client.close()
  await stopChrome(chrome)
}
