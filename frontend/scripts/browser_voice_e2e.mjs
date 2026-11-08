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
  }

  command(method, params = {}) {
    const id = this.nextId++
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  async evaluate(expression) {
    const response = await this.command('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
    })
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text)
    return response.result.value
  }

  close() {
    this.socket.close()
  }
}

async function waitFor(description, predicate, timeoutMs) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`browser-e2e: timed out waiting for ${description}`)
}

async function fetchJson(url, options) {
  const response = await fetch(url, options)
  if (!response.ok) throw new Error(`browser-e2e: ${url} returned ${response.status}`)
  return response.json()
}

async function openSocket(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url)
    socket.addEventListener('open', () => resolve(socket), { once: true })
    socket.addEventListener('error', () => reject(new Error('browser-e2e: CDP WebSocket failed')), { once: true })
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
  `--use-file-for-fake-audio-capture=${configuration.audio}`,
  `--host-resolver-rules=MAP local-voice-agent-e2e.local 127.0.0.1`,
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

  const target = await fetchJson(`${debuggingUrl}/json/new?${encodeURIComponent(configuration.url)}`, { method: 'PUT' })
  client = new CdpClient(await openSocket(target.webSocketDebuggerUrl))
  await client.command('Runtime.enable')
  await waitFor('login page', async () => (await client.evaluate("document.body.innerText.includes('本机管理员')")), configuration.timeoutSeconds * 1000)

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
  await waitFor('authenticated browser state', async () => (await client.evaluate("document.body.innerText.includes('已登录') && document.body.innerText.includes('连接语音')")), configuration.timeoutSeconds * 1000)
  console.log('browser-e2e: authenticated')

  await client.evaluate(`(() => {
    const button = [...document.querySelectorAll('button')].find((candidate) => candidate.textContent?.trim() === '连接语音')
    if (!button) throw new Error('voice connection control is missing')
    button.click()
  })()`)
  await waitFor('WebRTC connection', async () => (await client.evaluate("document.body.innerText.includes('断开')")), configuration.timeoutSeconds * 1000)
  console.log('browser-e2e: WebRTC connected')

  await waitFor('user transcript', async () => (await client.evaluate(`(() => {
    const rows = [...document.querySelectorAll('.transcript p')]
    return rows.some((row) => row.textContent?.startsWith('你') && row.textContent.trim() !== '你...')
  })()`)), configuration.timeoutSeconds * 1000)
  console.log('browser-e2e: user transcript observed')

  await waitFor('assistant transcript and audio latency', async () => (await client.evaluate(`(() => {
    const rows = [...document.querySelectorAll('.transcript p')]
    const assistant = rows.some((row) => row.textContent?.startsWith('助手') && row.textContent.trim() !== '助手...')
    const audio = rows.some((row) => row.textContent?.startsWith('发送首段音频'))
    return assistant && audio
  })()`)), configuration.timeoutSeconds * 1000)
  console.log('browser-e2e: assistant transcript and audio latency observed')

  await client.evaluate(`(() => {
    const button = [...document.querySelectorAll('button')].find((candidate) => candidate.textContent?.trim() === '断开')
    if (!button) throw new Error('voice disconnect control is missing')
    button.click()
  })()`)
  await waitFor('browser disconnect', async () => (await client.evaluate("document.body.innerText.includes('连接语音')")), configuration.timeoutSeconds * 1000)
  console.log('browser-e2e: disconnected')
  console.log('browser-e2e: passed')
} finally {
  if (client) {
    client.close()
  }
  await stopChrome(chrome)
}
