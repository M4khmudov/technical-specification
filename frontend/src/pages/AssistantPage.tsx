import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useParams } from 'react-router-dom'
import { api, ApiError, getToken } from '../api'
import type { ToolRecord } from '../chatStream'
import { streamAnswer } from '../chatStream'
import ActionCard from '../components/ActionCard'
import MemoryPanel from '../components/MemoryPanel'
import { downloadChatFile, isChatFileUrl } from '../chatFiles'
import type { ChatFileData, CodeRun } from '../chatFiles'
import { AuthImage, CodeRuns, FileChip, GeneratedFiles } from '../components/ChatFiles'
import type { ActionCardData } from '../components/ActionCard'
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
  actions?: ActionCardData[]
  code_runs?: CodeRun[]
  files?: ChatFileData[]
  error: string | null
  created_at: string
}
type ConversationDetail = Conversation & { messages: Message[] }

// Человеческие названия инструментов
const TOOL_LABELS: Record<string, string> = {
  get_material_stock: 'Остаток материала',
  get_deficit: 'Дефицит за период',
  list_jobs: 'Задания за период',
  get_batch_history: 'История партии',
  get_job_cost: 'Себестоимость задания',
  export_data: 'Данные для расчёта',
  remember_fact: 'Память',
  create_job: 'Создание задания',
}

const EXAMPLES = [
  'Чего не хватит на задания этой недели?',
  'Сколько свободного соевого воска?',
  'Какая себестоимость задания №27?',
  'Запланируй 200 свечей «Лаванда» на пятницу',
  'Построй график расхода воска по неделям за квартал',
  'Как изменится себестоимость свечи, если воск подорожает на 15%?',
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
  const files = message.files ?? []
  // картинки, уже вставленные в текст ответа, второй раз не показываем
  const shownInText = new Set(files.filter((f) => message.content.includes(`(${f.url})`)).map((f) => f.url))
  // Актуальный статус действий приходит с сервером; во время потока — из событий инструментов
  const cards: ActionCardData[] =
    message.actions && message.actions.length > 0
      ? message.actions
      : message.tools.flatMap((t) => (t.action ? [t.action as ActionCardData] : []))
  return (
    <div className="max-w-3xl">
      <div className="rounded-lg border border-stone-200 bg-white px-4 py-3">
        {message.content ? (
          <div className="prose-chat text-sm leading-relaxed">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                // Файлы Code Interpreter отдаются только с токеном
                img: ({ src, alt }) =>
                  isChatFileUrl(typeof src === 'string' ? src : undefined) ? <AuthImage src={src as string} alt={alt} /> : <img src={src as string} alt={alt ?? ''} />,
                a: ({ href, children }) =>
                  isChatFileUrl(href) ? (
                    <a
                      href={href}
                      onClick={(e) => {
                        e.preventDefault()
                        const file = files.find((f) => f.url === href)
                        void downloadChatFile(href, file?.filename ?? 'файл')
                      }}
                    >
                      {children}
                    </a>
                  ) : (
                    <a href={href} target="_blank" rel="noreferrer">
                      {children}
                    </a>
                  ),
              }}
            >
              {message.content}
            </ReactMarkdown>
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
        {message.tools
          .filter((t) => t.memory)
          .map((t, i) => (
            <div key={`mem-${i}`} className="mt-2 inline-block rounded bg-sky-50 px-2 py-1 text-xs text-sky-900">
              🧠 {t.summary}
            </div>
          ))}
        {cards.map((card) => (
          <ActionCard key={card.id} initial={card} />
        ))}
        <GeneratedFiles files={files} shownInText={shownInText} />
        <CodeRuns runs={message.code_runs ?? []} />
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
  const [attachments, setAttachments] = useState<ChatFileData[]>([])
  const [memoryVersion, setMemoryVersion] = useState(0)
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
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
      const attached = attachments
      setAttachments([])
      setMessages((prev) => [
        ...prev,
        { id: -1, role: 'user', content: question, status: 'complete', status_label: '', model: null, fallback_from: null, tools: [], files: attached, error: null, created_at: now },
        { id: -2, role: 'assistant', content: '', status: 'streaming', status_label: 'Отвечает…', model: null, fallback_from: null, tools: [], error: null, created_at: now },
      ])
      setText('')
      const controller = new AbortController()
      abortRef.current = controller
      await streamAnswer(
        cid,
        question,
        attached.map((f) => f.id),
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
          onCode: (run) => patchLast((m) => ({ ...m, code_runs: [...(m.code_runs ?? []), run] })),
          onFile: (file) => patchLast((m) => ({ ...m, files: [...(m.files ?? []), file] })),
          onDone: (d) =>
            patchLast((m) => ({
              ...m,
              content: d.content ?? m.content,
              code_runs: d.code_runs,
              files: d.files,
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
      setMemoryVersion((v) => v + 1) // ассистент мог что-то запомнить
    }
  }

  // Файл загружается сразу и прикрепляется к следующему вопросу
  const attach = async (file: File) => {
    setError(null)
    setUploading(true)
    try {
      let cid = conversationId
      if (!cid) {
        const created = await api<Conversation>('/chat/conversations', { method: 'POST', body: {} })
        cid = created.id
        navigate(`/assistant/${cid}`, { replace: true })
        list.reload()
      }
      const form = new FormData()
      form.append('file', file)
      const response = await fetch(`/api/chat/conversations/${cid}/files`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${getToken() ?? ''}` },
        body: form,
      })
      const body = await response.json()
      if (!response.ok) throw new ApiError(response.status, body.detail ?? 'Не удалось загрузить файл')
      setAttachments((prev) => [...prev, body as ChatFileData])
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Не удалось загрузить файл')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
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
        <MemoryPanel version={memoryVersion} />
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
              <div key={`${m.id}-${i}`} className="flex flex-col items-end gap-1">
                <div className="max-w-2xl whitespace-pre-wrap rounded-lg bg-amber-700 px-4 py-2 text-sm text-white">{m.content}</div>
                {(m.files ?? []).length > 0 && (
                  <div className="flex flex-wrap justify-end gap-1">
                    {(m.files ?? []).map((f) => (
                      <FileChip key={f.id} file={f} />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <AssistantMessage key={`${m.id}-${i}`} message={m} />
            ),
          )}
          <div ref={bottomRef} />
        </div>
        <ErrorBanner message={error ?? detail.error} />
        {attachments.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 border-t border-stone-200 bg-white px-3 pt-2 text-xs">
            <span className="text-stone-500">Прикреплено:</span>
            {attachments.map((f) => (
              <span key={f.id} className="inline-flex items-center gap-1 rounded bg-amber-50 px-2 py-0.5">
                📎 {f.filename}
                <button type="button" aria-label={`Убрать ${f.filename}`} onClick={() => setAttachments(attachments.filter((a) => a.id !== f.id))}>
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <form onSubmit={submit} className="flex gap-2 border-t border-stone-200 bg-white p-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && attach(e.target.files[0])}
          />
          <Button type="button" disabled={busy || uploading} onClick={() => fileInputRef.current?.click()} title="Прикрепить CSV или Excel для анализа" aria-label="Прикрепить файл">
            {uploading ? '…' : '📎'}
          </Button>
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
