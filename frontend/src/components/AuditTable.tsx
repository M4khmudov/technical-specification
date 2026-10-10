import type { AuditEntry } from '../api'
import { AUDIT_ACTION, fmtDateTime } from '../format'

export function AuditValue({ value }: { value: Record<string, unknown> | null }) {
  if (!value) return <span className="text-stone-400">—</span>
  return (
    <ul className="space-y-0.5">
      {Object.entries(value).map(([key, v]) => (
        <li key={key}>
          <span className="text-stone-500">{key}:</span>{' '}
          {v && typeof v === 'object' ? (
            <AuditValue value={v as Record<string, unknown>} />
          ) : (
            <span className="font-mono text-xs">{String(v)}</span>
          )}
        </li>
      ))}
    </ul>
  )
}

// Журнал изменений: кто, когда, что было, что стало
export default function AuditTable({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <p className="text-sm text-stone-500">Изменений нет</p>
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-sm">
        <thead className="text-left text-xs uppercase text-stone-500">
          <tr>
            <th className="py-1 pr-4">Когда</th>
            <th className="py-1 pr-4">Кто</th>
            <th className="py-1 pr-4">Действие</th>
            <th className="py-1 pr-4">Было</th>
            <th className="py-1">Стало</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => (
            <tr key={i} className="border-t border-stone-100 align-top">
              <td className="whitespace-nowrap py-2 pr-4">{fmtDateTime(e.created_at)}</td>
              <td className="py-2 pr-4">{e.user_full_name}</td>
              <td className="py-2 pr-4">{AUDIT_ACTION[e.action] ?? e.action}</td>
              <td className="py-2 pr-4"><AuditValue value={e.before} /></td>
              <td className="py-2"><AuditValue value={e.after} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
