import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import type { ToolRecord } from '../chatStream'
import { streamAnswer } from '../chatStream'
import { Button, ErrorBanner } from '../components/ui'
import { fmtDateTime } from '../format'
import { useApi } from '../useApi'

type Conversation = { id: number; title: string; created_at: string; updated_at: string }
type Message = {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: 'streaming' | 'complete' | 'stopped' | 'interrupted' | 'error'
  status_label: string
  model: string | null
  fallback_from: string | null
  tools: ToolRecord[]
  error: string | null
  created_at: string
}
type ConversationDetail = Conversation & { messages: Message[] }

// Человеческие названия инструментов (наполняются вместе с инструментами)
const TOOL_LABELS: Record<string, string> = {}

const EXAMPLES = [
  'Чего не хватит на задания этой недели?',
  'Сколько свободного соевого воска?',
  'Какая себестоимость задания №27?',
]

function ToolChips({ tools }: { tools: ToolRecord[] }) {
  const [open, setOpen] = useState(false)
  if (tools.length === 0) return null
  return (
    <div className="mt-2">
      <button className="text-xs text-stone-500 hover:text-stone-800" onClick={() => setOpen(!open)}>
        🔧 Инструменты: {tools.map((t) => TOOL_LABELS[t.name] ?? t.name).join(', ')} {open ? '▲' : '▼'}
      </button>
      {open && (
        <ul className="mt-1 space-y-1 text-xs">
          {tools.map((t, i) => (
            <li key={i} className={`rounded border px-2 py-1 ${t.ok ? 'border-stone-200 bg-stone-50' : 'border-red-200 bg-red-50'}`}>
              <span className="font-medium">{TOOL_LABELS[t.name] ?? t.name}</span>
              {t.arguments && <span className="ml-1 font-mono text-stone-500">{JSON.stringify(t.arguments)}</span>}
              <div className="text-stone-600">{t.status === 'started' ? 'выполняется…' : t.summary}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AssistantMessage({ message }: { message: Message }) {
  const failed = ['stopped', 'interrupted', 'error'].includes(message.status)
  return (
    <div className="max-w-3xl">
      <div className="rounded-lg border border-stone-200 bg-white px-4 py-3">
        {message.content ? (
          <div className="prose-chat text-sm leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
          </div>
        ) : message.status === 'streaming' ? (
          <span className="text-sm text-stone-400">Думаю…</span>
        ) : null}
        {message.status === 'streaming' && message.content && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-amber-600 align-middle" />}
        {failed && (
          <div className={`mt-2 rounded px-2 py-1 text-xs ${message.status === 'stopped' ? 'bg-stone-100 text-stone-600' : 'bg-red-50 text-red-800'}`}>
            {message.status_label}
            {message.error && `: ${message.error}`}
          </div>
        )}
        <ToolChips tools={message.tools} />
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 px-1 text-xs text-stone-400">
        {message.model && <span>Модель: {message.model}</span>}
        {message.fallback_from && <span className="text-amber-700">основная модель ({message.fallback_from}) была недоступна</span>}
        <span>{fmtDateTime(message.created_at)}</span>
      </div>
    </div>
  )
}

export default function AssistantPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const conversationId = id ? Number(id) : null
  const list = useApi<Conversation[]>('/chat/conversations')
  const detail = useApi<ConversationDetail>(conversationId ? `/chat/conversations/${conversationId}` : null)
  const [messages, setMessages] = useState<Message[]>([])
  const [text, setText] = useState('')
  const [streamingId, setStreamingId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  // Пока идёт ответ, сообщения ведёт поток: загрузка диалога с сервера их не затирает
  const busyRef = useRef(false)
  useEffect(() => {
    if (busyRef.current) return
    setMessages(conversationId && detail.data?.id === conversationId ? detail.data.messages : [])
  }, [detail.data, conversationId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  // Уход со страницы во время ответа — закрываем поток (сервер сохранит ответ как оборванный)
  useEffect(() => () => abortRef.current?.abort(), [])

  const patchLast = useCallback((patch: (m: Message) => Message) => {
    setMessages((prev) => {
      if (prev.length === 0) return prev
      const next = [...prev]
      next[next.length - 1] = patch(next[next.length - 1])
      return next
    })
  }, [])

  const send = async (question: string) => {
    if (!question.trim() || busy) return
    setError(null)
    setBusy(true)
    busyRef.current = true
    let cid = conversationId
    try {
      if (!cid) {
        const created = await api<Conversation>('/chat/conversations', { method: 'POST', body: {} })
        cid = created.id
        navigate(`/assistant/${cid}`, { replace: true })
      }
      const now = new Date().toISOString()
      setMessages((prev) => [
        ...prev,
        { id: -1, role: 'user', content: question, status: 'complete', status_label: '', model: null, fallback_from: null, tools: [], error: null, created_at: now },
        { id: -2, role: 'assistant', content: '', status: 'streaming', status_label: 'Отвечает…', model: null, fallback_from: null, tools: [], error: null, created_at: now },
      ])
      setText('')
      const controller = new AbortController()
      abortRef.current = controller
      await streamAnswer(
        cid,
        question,
        {
          onStart: (d) => {
            setStreamingId(d.message_id)
            patchLast((m) => ({ ...m, id: d.message_id }))
          },
          onModel: (d) => patchLast((m) => ({ ...m, model: d.model, fallback_from: d.fallback_from })),
          onDelta: (delta) => patchLast((m) => ({ ...m, content: m.content + delta })),
          onTool: (tool) =>
            patchLast((m) => {
              const tools = tool.status === 'finished' ? [...m.tools.filter((t) => t.status !== 'started' || t.name !== tool.name), tool] : [...m.tools, tool]
              return { ...m, tools }
            }),
          onDone: (d) =>
            patchLast((m) => ({
              ...m,
              status: d.status as Message['status'],
              status_label: d.status_label,
              error: d.error,
              model: d.model ?? m.model,
              fallback_from: d.fallback_from,
              tools: d.tools,
            })),
        },
        controller.signal,
      )
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(e instanceof ApiError ? e.message : 'Не удалось получить ответ')
        patchLast((m) => (m.status === 'streaming' ? { ...m, status: 'interrupted', status_label: 'Ответ оборван' } : m))
      }
    } finally {
      abortRef.current = null
      busyRef.current = false
      setStreamingId(null)
      setBusy(false)
      list.reload()
    }
  }

  const stop = async () => {
    if (streamingId === null) {
      abortRef.current?.abort()
      return
    }
    try {
      // Сервер сохранит ответ как «Остановлено» и закроет поток событием done
      const result = await api<{ stopped: boolean }>(`/chat/messages/${streamingId}/stop`, { method: 'POST' })
      if (!result.stopped) abortRef.current?.abort()
    } catch {
      abortRef.current?.abort()
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void send(text)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(text)
    }
  }

  const remove = async (cid: number) => {
    if (!window.confirm('Удалить диалог?')) return
    await api(`/chat/conversations/${cid}`, { method: 'DELETE' })
    list.reload()
    if (cid === conversationId) navigate('/assistant')
  }

  return (
    <div className="grid gap-4 md:grid-cols-[16rem_1fr]">
      <aside className="rounded-lg border border-stone-200 bg-white p-2 md:h-[calc(100vh-8rem)] md:overflow-y-auto">
        <Button variant="primary" className="mb-2 w-full" disabled={busy} onClick={() => navigate('/assistant')}>
          + Новый диалог
        </Button>
        <ul className="space-y-0.5">
          {list.data?.map((c) => (
            <li key={c.id} className="group flex items-center">
              <button
                onClick={() => navigate(`/assistant/${c.id}`)}
                disabled={busy}
                className={`flex-1 truncate rounded px-2 py-1.5 text-left text-sm ${c.id === conversationId ? 'bg-amber-100 text-amber-900' : 'hover:bg-stone-100'}`}
                title={c.title}
              >
                {c.title}
              </button>
              <button onClick={() => remove(c.id)} disabled={busy} className="px-1 text-xs text-stone-400 opacity-0 hover:text-red-700 group-hover:opacity-100" aria-label={`Удалить диалог «${c.title}»`}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <section className="flex min-h-[70vh] flex-col rounded-lg border border-stone-200 bg-stone-50 md:h-[calc(100vh-8rem)]">
        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && (
            <div className="mx-auto mt-10 max-w-md text-center">
              <h1 className="mb-2 text-xl font-semibold">ИИ-ассистент мастерской</h1>
              <p className="mb-4 text-sm text-stone-600">Спросите про остатки, дефицит, себестоимость или попросите запланировать задание.</p>
              <div className="flex flex-col gap-2">
                {EXAMPLES.map((q) => (
                  <Button key={q} onClick={() => send(q)} disabled={busy}>
                    {q}
                  </Button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) =>
            m.role === 'user' ? (
              <div key={`${m.id}-${i}`} className="flex justify-end">
                <div className="max-w-2xl whitespace-pre-wrap rounded-lg bg-amber-700 px-4 py-2 text-sm text-white">{m.content}</div>
              </div>
            ) : (
              <AssistantMessage key={`${m.id}-${i}`} message={m} />
            ),
          )}
          <div ref={bottomRef} />
        </div>
        <ErrorBanner message={error ?? detail.error} />
        <form onSubmit={submit} className="flex gap-2 border-t border-stone-200 bg-white p-3">
          <textarea
            className="flex-1 resize-none rounded-md border border-stone-300 px-3 py-2 text-sm focus:border-amber-600 focus:outline-none"
            rows={2}
            placeholder="Спросите ассистента… (Enter — отправить, Shift+Enter — новая строка)"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            maxLength={4000}
            aria-label="Вопрос ассистенту"
          />
          {busy ? (
            <Button type="button" variant="danger" onClick={stop}>
              ■ Остановить
            </Button>
          ) : (
            <Button type="submit" variant="primary" disabled={!text.trim()}>
              Отправить
            </Button>
          )}
        </form>
      </section>
    </div>
  )
}
