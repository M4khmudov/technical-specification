import { useEffect, useState } from 'react'
import { api } from '../api'
import { useApi } from '../useApi'
import { useConfirm } from '../useConfirm'

type Fact = { id: number; text: string; created_at: string }

// Что ассистент помнит о пользователе. Только свои факты; любой можно удалить.
export default function MemoryPanel({ version }: { version: number }) {
  const { data, error, reload } = useApi<Fact[]>('/memory')
  const [open, setOpen] = useState(false)
  const [confirm, confirmDialog] = useConfirm()

  useEffect(() => {
    if (version > 0) reload()
  }, [version, reload])

  const remove = async (fact: Fact) => {
    const ok = await confirm({
      title: 'Забыть этот факт?',
      message: (
        <>
          Ассистент перестанет учитывать «<span className="font-medium text-stone-800">{fact.text}</span>» в
          следующих ответах.
        </>
      ),
      confirmText: 'Забыть',
      danger: true,
    })
    if (!ok) return
    await api(`/memory/${fact.id}`, { method: 'DELETE' })
    reload()
  }

  const facts = data ?? []
  return (
    <div className="mt-3 border-t border-stone-200 pt-2">
      {confirmDialog}
      <button className="w-full px-2 text-left text-xs font-medium text-stone-600 hover:text-stone-900" onClick={() => setOpen(!open)}>
        🧠 Память ассистента ({facts.length}) {open ? '▲' : '▼'}
      </button>
      {open && (
        <div className="mt-1 px-2 text-xs">
          {error && <p className="text-red-700">{error}</p>}
          {facts.length === 0 ? (
            <p className="text-stone-500">Пока ничего. Скажите, например: «Запомни: считай в евро».</p>
          ) : (
            <ul className="space-y-1">
              {facts.map((f) => (
                <li key={f.id} className="group flex items-start gap-1">
                  <span className="flex-1">{f.text}</span>
                  <button className="text-stone-400 hover:text-red-700" onClick={() => remove(f)} aria-label={`Забыть «${f.text}»`} title="Забыть">
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
