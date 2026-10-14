// Чтение SSE-ответа ассистента. EventSource не умеет POST с заголовком авторизации,
// поэтому читаем поток fetch вручную и разбираем события «event: …\ndata: …».

import { ApiError, getToken } from './api'
import type { ChatFileData, CodeRun } from './chatFiles'

export type ToolRecord = {
  name: string
  arguments: Record<string, unknown> | null
  ok: boolean
  summary: string
  status?: 'started' | 'finished'
  [key: string]: unknown
}

export type StreamHandlers = {
  onStart?: (data: { message_id: number }) => void
  onModel?: (data: { model: string; provider: string; fallback_from: string | null }) => void
  onDelta?: (text: string) => void
  onTool?: (data: ToolRecord) => void
  onCode?: (data: CodeRun) => void
  onFile?: (data: ChatFileData) => void
  onDone?: (data: {
    message_id: number
    status: string
    status_label: string
    error: string | null
    model: string | null
    fallback_from: string | null
    tools: ToolRecord[]
    code_runs: CodeRun[]
    notes: string[]
    content: string
    files: ChatFileData[]
  }) => void
}

export async function streamAnswer(
  conversationId: number,
  text: string,
  fileIds: number[],
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/chat/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getToken() ?? ''}` },
    body: JSON.stringify({ text, file_ids: fileIds }),
    signal,
  })
  if (!response.ok || !response.body) {
    let message = `Ошибка ${response.status}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // не JSON
    }
    throw new ApiError(response.status, message)
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      dispatch(buffer.slice(0, boundary), handlers)
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
  }
}

function dispatch(block: string, handlers: StreamHandlers) {
  let event = ''
  let data = ''
  for (const line of block.split('\n')) {
    if (line.startsWith(':')) continue // keep-alive
    if (line.startsWith('event: ')) event = line.slice(7)
    else if (line.startsWith('data: ')) data += line.slice(6)
  }
  if (!event || !data) return
  const payload = JSON.parse(data)
  switch (event) {
    case 'start':
      handlers.onStart?.(payload)
      break
    case 'model':
      handlers.onModel?.(payload)
      break
    case 'delta':
      handlers.onDelta?.(payload.text)
      break
    case 'tool':
      handlers.onTool?.(payload)
      break
    case 'code':
      handlers.onCode?.(payload)
      break
    case 'file':
      handlers.onFile?.(payload)
      break
    case 'done':
      handlers.onDone?.(payload)
      break
  }
}

// Ответ, у которого поток закончился без события done, — не «отвечает», а оборван
export function settleUnfinished<T extends { role: string; status: string; status_label: string }>(m: T): T {
  if (m.role !== 'assistant' || m.status !== 'streaming') return m
  return { ...m, status: 'interrupted', status_label: 'Ответ оборван' }
}
