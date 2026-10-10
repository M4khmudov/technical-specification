import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { fmtDate, fmtQty, isPositive } from '../format'
import { useAction } from '../useApi'
import { Badge, Button, ErrorBanner } from './ui'

export type ActionCardData = {
  id: number
  kind: string
  status: 'pending' | 'confirmed' | 'rejected' | 'expired' | 'failed'
  status_label: string
  preview: {
    title: string
    recipe: string
    quantity: number
    planned_on: string
    comment: string | null
    will_have_deficit: boolean
    materials: { code: string; name: string; unit: string; required: string; free_now: string; expected_deficit: string }[]
  }
  result: { job_id: number; job_number: number; has_deficit: boolean; status_label: string } | null
  error: string | null
}

const STATUS_STYLE: Record<string, string> = {
  pending: 'planned',
  confirmed: 'accepted',
  rejected: 'cancelled',
  expired: 'cancelled',
  failed: 'rejected',
}

// Действие, предложенное ассистентом. Данные меняются только по кнопке «Подтвердить».
export default function ActionCard({ initial }: { initial: ActionCardData }) {
  const [card, setCard] = useState(initial)
  const { busy, error, run } = useAction()
  const { preview } = card

  const decide = async (decision: 'confirm' | 'reject') => {
    const updated = await run(() => api<ActionCardData>(`/chat/actions/${card.id}/${decision}`, { method: 'POST' }))
    if (updated) setCard(updated)
  }

  return (
    <div className="mt-3 rounded-lg border-2 border-amber-300 bg-amber-50/60 p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">
          {preview.recipe}, {preview.quantity}&nbsp;шт на {fmtDate(preview.planned_on)}
        </span>
        <Badge status={STATUS_STYLE[card.status]}>{card.status_label}</Badge>
      </div>
      <p className="mb-2 text-xs text-stone-600">
        Будет создано задание и сразу запланировано: материалы зарезервируются по FIFO.
        {preview.comment && ` Комментарий: ${preview.comment}`}
      </p>
      <table className="mb-2 w-full text-xs">
        <thead className="text-left text-stone-500">
          <tr>
            <th className="py-0.5 pr-2">Материал</th>
            <th className="py-0.5 pr-2 text-right">Нужно</th>
            <th className="py-0.5 pr-2 text-right">Свободно сейчас</th>
            <th className="py-0.5 text-right">Не хватит</th>
          </tr>
        </thead>
        <tbody>
          {preview.materials.map((m) => (
            <tr key={m.code} className="border-t border-amber-200/70">
              <td className="py-0.5 pr-2">{m.name}</td>
              <td className="whitespace-nowrap py-0.5 pr-2 text-right">{fmtQty(m.required, m.unit)}</td>
              <td className="whitespace-nowrap py-0.5 pr-2 text-right">{fmtQty(m.free_now, m.unit)}</td>
              <td className={`whitespace-nowrap py-0.5 text-right ${isPositive(m.expected_deficit) ? 'font-medium text-red-700' : 'text-stone-400'}`}>
                {isPositive(m.expected_deficit) ? fmtQty(m.expected_deficit, m.unit) : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {preview.will_have_deficit && card.status === 'pending' && (
        <p className="mb-2 text-xs text-red-800">Материалов хватит не на всё — задание будет запланировано с дефицитом, запустить его можно будет после поступления.</p>
      )}
      <ErrorBanner message={error ?? card.error} />
      {card.status === 'pending' && (
        <div className="flex gap-2">
          <Button variant="primary" disabled={busy} onClick={() => decide('confirm')}>
            Подтвердить
          </Button>
          <Button disabled={busy} onClick={() => decide('reject')}>
            Отменить
          </Button>
        </div>
      )}
      {card.result && (
        <p className="text-sm">
          Создано{' '}
          <Link to={`/jobs/${card.result.job_id}`} className="font-medium text-amber-800 hover:underline">
            задание №{card.result.job_number}
          </Link>{' '}
          — {card.result.status_label.toLowerCase()}
          {card.result.has_deficit ? ', есть дефицит' : ', всё в резерве'}.
        </p>
      )}
    </div>
  )
}
