import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, query } from '../api'
import type { Job, JobListItem, Page, Recipe } from '../api'
import { useAuth } from '../auth'
import { Badge, Button, Card, ErrorBanner, Field, inputClass, Pagination } from '../components/ui'
import { fmtDate, todayIso } from '../format'
import { useAction, useApi } from '../useApi'

const STATUSES: Record<string, string> = {
  draft: 'Черновик',
  planned: 'Запланировано',
  in_progress: 'В работе',
  completed: 'Завершено',
  marked: 'Сервис маркировки',
  cancelled: 'Отменено',
}

function NewJobForm({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { data: recipes } = useApi<Recipe[]>('/recipes')
  const [recipeId, setRecipeId] = useState('')
  const [quantity, setQuantity] = useState('100')
  const [plannedOn, setPlannedOn] = useState(todayIso())
  const [plan, setPlan] = useState(true)
  const { busy, error, run } = useAction()

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const job = await run(() =>
      api<Job>(`/jobs${query({ plan })}`, {
        method: 'POST',
        body: { recipe_id: Number(recipeId), quantity: Number(quantity), planned_on: plannedOn },
      }),
    )
    if (job) navigate(`/jobs/${job.id}`)
  }

  return (
    <Card title="Новое задание">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-4">
        <Field label="Рецепт">
          <select className={inputClass} value={recipeId} onChange={(e) => setRecipeId(e.target.value)} required>
            <option value="">Выберите…</option>
            {recipes?.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Количество, шт">
          <input
            type="number"
            min={1}
            className={inputClass}
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            required
          />
        </Field>
        <Field label="Дата">
          <input
            type="date"
            min={todayIso()}
            className={inputClass}
            value={plannedOn}
            onChange={(e) => setPlannedOn(e.target.value)}
            required
          />
        </Field>
        <label className="flex items-end gap-2 pb-2 text-sm">
          <input type="checkbox" checked={plan} onChange={(e) => setPlan(e.target.checked)} />
          Сразу запланировать и зарезервировать
        </label>
        <div className="flex gap-2 sm:col-span-4">
          <Button variant="primary" type="submit" disabled={busy}>
            Создать
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
      <ErrorBanner message={error} />
    </Card>
  )
}

export default function JobsPage() {
  const { can } = useAuth()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(false)
  const page = Number(params.get('page') ?? '1')
  const { data, error, loading } = useApi<Page<JobListItem>>(
    `/jobs${query({
      status: params.get('status'),
      planned_from: params.get('planned_from'),
      planned_to: params.get('planned_to'),
      page,
      size: 20,
    })}`,
  )

  const set = (key: string, value: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      if (key !== 'page') next.delete('page')
      return next
    })

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Производственные задания</h1>
        {can('job.manage') && !creating && (
          <Button variant="primary" onClick={() => setCreating(true)}>
            + Новое задание
          </Button>
        )}
      </div>
      {creating && <NewJobForm onClose={() => setCreating(false)} />}

      <div className="mb-3 grid gap-3 sm:grid-cols-4">
        <Field label="Статус">
          <select className={inputClass} value={params.get('status') ?? ''} onChange={(e) => set('status', e.target.value)}>
            <option value="">Все</option>
            {Object.entries(STATUSES).map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Дата с">
          <input type="date" className={inputClass} value={params.get('planned_from') ?? ''} onChange={(e) => set('planned_from', e.target.value)} />
        </Field>
        <Field label="Дата по">
          <input type="date" className={inputClass} value={params.get('planned_to') ?? ''} onChange={(e) => set('planned_to', e.target.value)} />
        </Field>
        <div className="flex items-end">
          <Button onClick={() => setParams({ planned_from: todayIso() })}>С сегодняшнего дня</Button>
        </div>
      </div>

      <ErrorBanner message={error} />
      <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-stone-100 text-left text-xs uppercase tracking-wide text-stone-600">
            <tr>
              <th className="px-3 py-2">№</th>
              <th className="px-3 py-2">Изделие</th>
              <th className="px-3 py-2 text-right">Кол-во</th>
              <th className="px-3 py-2">Дата</th>
              <th className="px-3 py-2">Статус</th>
              <th className="px-3 py-2">Материалы</th>
            </tr>
          </thead>
          <tbody className={loading ? 'opacity-50' : ''}>
            {data?.items.map((j) => (
              <tr key={j.id} className="border-t border-stone-100 hover:bg-amber-50/40">
                <td className="px-3 py-2">
                  <Link to={`/jobs/${j.id}`} className="font-medium text-amber-800 hover:underline">
                    {j.number}
                  </Link>
                </td>
                <td className="px-3 py-2">{j.recipe_name}</td>
                <td className="px-3 py-2 text-right">{j.quantity}&nbsp;шт</td>
                <td className="whitespace-nowrap px-3 py-2">{fmtDate(j.planned_on)}</td>
                <td className="px-3 py-2">
                  <Badge status={j.status}>{j.status_label}</Badge>
                </td>
                <td className="px-3 py-2">
                  {j.has_deficit ? (
                    <span className="text-red-700">Дефицит</span>
                  ) : ['draft', 'planned', 'in_progress'].includes(j.status) ? (
                    <span className="text-green-700">Всё в резерве</span>
                  ) : (
                    <span className="text-stone-400">—</span>
                  )}
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-stone-500">
                  Заданий не найдено
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {data && <Pagination page={data.page} size={data.size} total={data.total} onPage={(p) => set('page', String(p))} />}
    </div>
  )
}
