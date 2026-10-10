import { Link, useSearchParams } from 'react-router-dom'
import { query } from '../api'
import type { AuditListEntry, Page } from '../api'
import { AuditValue } from '../components/AuditTable'
import { ErrorBanner, Field, inputClass, Pagination } from '../components/ui'
import { AUDIT_ACTION, fmtDateTime } from '../format'
import { useApi } from '../useApi'

const ENTITY: Record<string, string> = { batch: 'Партия', job: 'Задание', marking_report: 'Отчёт маркировки' }

export default function AuditPage() {
  const [params, setParams] = useSearchParams()
  const page = Number(params.get('page') ?? '1')
  const { data, error, loading } = useApi<Page<AuditListEntry>>(
    `/audit${query({
      entity_type: params.get('entity_type'),
      date_from: params.get('date_from'),
      date_to: params.get('date_to'),
      page,
      size: 50,
    })}`,
  )
  const set = (key: string, v: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (v) next.set(key, v)
      else next.delete(key)
      if (key !== 'page') next.delete('page')
      return next
    })

  return (
    <div>
      <h1 className="mb-4 text-2xl font-semibold">Журнал изменений</h1>
      <div className="mb-3 grid gap-3 sm:grid-cols-4">
        <Field label="Объект">
          <select className={inputClass} value={params.get('entity_type') ?? ''} onChange={(e) => set('entity_type', e.target.value)}>
            <option value="">Все</option>
            {Object.entries(ENTITY).map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="С">
          <input type="date" className={inputClass} value={params.get('date_from') ?? ''} onChange={(e) => set('date_from', e.target.value)} />
        </Field>
        <Field label="По">
          <input type="date" className={inputClass} value={params.get('date_to') ?? ''} onChange={(e) => set('date_to', e.target.value)} />
        </Field>
      </div>
      <ErrorBanner message={error} />
      <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-stone-100 text-left text-xs uppercase text-stone-600">
            <tr>
              <th className="px-3 py-2">Когда</th>
              <th className="px-3 py-2">Кто</th>
              <th className="px-3 py-2">Объект</th>
              <th className="px-3 py-2">Действие</th>
              <th className="px-3 py-2">Было</th>
              <th className="px-3 py-2">Стало</th>
            </tr>
          </thead>
          <tbody className={loading ? 'opacity-50' : ''}>
            {data?.items.map((e) => (
              <tr key={e.id} className="border-t border-stone-100 align-top">
                <td className="whitespace-nowrap px-3 py-2">{fmtDateTime(e.created_at)}</td>
                <td className="px-3 py-2">{e.user_full_name}</td>
                <td className="whitespace-nowrap px-3 py-2">
                  {e.entity_type === 'batch' || e.entity_type === 'job' ? (
                    <Link to={`/${e.entity_type === 'batch' ? 'batches' : 'jobs'}/${e.entity_id}`} className="text-amber-800 hover:underline">
                      {ENTITY[e.entity_type]} #{e.entity_id}
                    </Link>
                  ) : (
                    `${ENTITY[e.entity_type] ?? e.entity_type} #${e.entity_id}`
                  )}
                </td>
                <td className="px-3 py-2">{AUDIT_ACTION[e.action] ?? e.action}</td>
                <td className="max-w-xs break-words px-3 py-2"><AuditValue value={e.before} /></td>
                <td className="max-w-xs break-words px-3 py-2"><AuditValue value={e.after} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && <Pagination page={data.page} size={data.size} total={data.total} onPage={(p) => set('page', String(p))} />}
    </div>
  )
}
