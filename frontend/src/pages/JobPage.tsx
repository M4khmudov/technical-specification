import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import type { AuditEntry, Job, Marking } from '../api'
import { useAuth } from '../auth'
import AuditTable from '../components/AuditTable'
import { useConfirm } from '../useConfirm'
import type { ConfirmOptions } from '../components/ConfirmDialog'
import { Badge, Button, Card, ErrorBanner, inputClass, Loading, TrafficLight } from '../components/ui'
import { fmtDate, fmtDateTime, fmtMoney, fmtQty, isPositive } from '../format'
import { useAction, useApi } from '../useApi'

function CompleteForm({ job, onDone, onCancel }: { job: Job; onDone: (job: Job) => void; onCancel: () => void }) {
  // По умолчанию факт = норма; технолог правит только то, что отличается
  const [actual, setActual] = useState<Record<number, string>>(
    Object.fromEntries(job.materials.map((m) => [m.material_id, m.required_qty])),
  )
  const { busy, error, run } = useAction()

  const submit = async () => {
    const result = await run(() =>
      api<Job>(`/jobs/${job.id}/complete`, {
        method: 'POST',
        body: {
          actuals: job.materials.map((m) => ({
            material_id: m.material_id,
            actual_qty: actual[m.material_id] || '0',
          })),
        },
      }),
    )
    if (result) onDone(result)
  }

  return (
    <Card title="Завершение: фактический расход">
      <p className="mb-3 text-sm text-stone-600">
        Перерасход досписывается из свободного остатка по FIFO, экономия возвращается в партии.
      </p>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        {job.materials.map((m) => (
          <label key={m.material_id} className="text-sm">
            <span className="mb-1 block text-stone-600">
              {m.material_name}, {m.unit} <span className="text-stone-400">(норма {fmtQty(m.required_qty)})</span>
            </span>
            <input
              className={inputClass}
              inputMode="decimal"
              value={actual[m.material_id]}
              onChange={(e) => setActual({ ...actual, [m.material_id]: e.target.value.replace(',', '.') })}
            />
          </label>
        ))}
      </div>
      <ErrorBanner message={error} />
      <div className="mt-3 flex gap-2">
        <Button variant="primary" disabled={busy} onClick={submit}>
          Завершить задание
        </Button>
        <Button onClick={onCancel}>Отмена</Button>
      </div>
    </Card>
  )
}

function MarkingCard({ jobId, onDelivered }: { jobId: number; onDelivered: () => void }) {
  const { can } = useAuth()
  const { data, error, reload } = useApi<Marking>(`/jobs/${jobId}/marking`)
  const action = useAction()
  const status = data?.status

  // Пока отчёт отправляется в фоне — обновляем статус каждые 3 секунды
  useEffect(() => {
    if (status !== 'pending') return
    const timer = setInterval(reload, 3000)
    return () => clearInterval(timer)
  }, [status, reload])

  // Отчёт принят — статус задания сменился на «Сервис маркировки»
  const [seenStatus, setSeenStatus] = useState(status)
  if (status !== seenStatus) {
    setSeenStatus(status)
    if (seenStatus === 'pending' && status === 'sent') onDelivered()
  }

  if (!data) return error ? <ErrorBanner message={error} /> : null

  return (
    <Card
      title="Сервис маркировки"
      actions={
        <>
          <Button onClick={reload}>Обновить</Button>
          {data.status === 'failed' && can('marking.retry') && (
            <Button
              variant="primary"
              disabled={action.busy}
              onClick={() => action.run(() => api(`/jobs/${jobId}/marking/retry`, { method: 'POST' })).then(reload)}
            >
              Повторить отправку
            </Button>
          )}
        </>
      }
    >
      <ErrorBanner message={action.error} />
      <p className="mb-3 text-sm">
        <Badge status={data.status}>{data.status_label}</Badge>
        {data.status === 'pending' && data.next_attempt_at && (
          <span className="ml-2 text-stone-600">следующая попытка {fmtDateTime(data.next_attempt_at)}</span>
        )}
        {data.sent_at && <span className="ml-2 text-stone-600">{fmtDateTime(data.sent_at)}</span>}
        <span className="ml-2 font-mono text-xs text-stone-400">ключ {data.idempotency_key}</span>
      </p>
      {data.attempts.length > 0 && (
        <table className="min-w-full text-sm">
          <thead className="text-left text-xs uppercase text-stone-500">
            <tr>
              <th className="py-1 pr-4">#</th>
              <th className="py-1 pr-4">Когда</th>
              <th className="py-1 pr-4">Ответ</th>
              <th className="py-1 pr-4 text-right">Длительность</th>
              <th className="py-1">Ошибка</th>
            </tr>
          </thead>
          <tbody>
            {data.attempts.map((a) => (
              <tr key={a.attempt_no} className="border-t border-stone-100">
                <td className="py-1.5 pr-4">{a.attempt_no}</td>
                <td className="whitespace-nowrap py-1.5 pr-4">{fmtDateTime(a.started_at)}</td>
                <td className="py-1.5 pr-4">{a.http_status ?? 'нет ответа'}</td>
                <td className="py-1.5 pr-4 text-right">{a.duration_ms !== null ? `${a.duration_ms} мс` : '—'}</td>
                <td className="py-1.5 text-stone-600">{a.error ?? ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}

export default function JobPage() {
  const { id } = useParams()
  const { can } = useAuth()
  const { data: job, setData, error, loading, reload } = useApi<Job>(`/jobs/${id}`)
  const history = useApi<AuditEntry[]>(`/jobs/${id}/history`)
  const action = useAction()
  const [completing, setCompleting] = useState(false)
  const [confirm, confirmDialog] = useConfirm()

  if (loading && !job) return <Loading />
  if (!job) return <ErrorBanner message={error} />

  const act = async (path: string, confirmOptions?: ConfirmOptions) => {
    if (confirmOptions && !(await confirm(confirmOptions))) return
    const result = await action.run(() => api<Job>(`/jobs/${job.id}/${path}`, { method: 'POST' }))
    if (result) {
      setData(result)
      history.reload()
    }
  }

  const manage = can('job.manage')
  const runJobs = can('job.run')
  const active = ['draft', 'planned', 'in_progress'].includes(job.status)

  return (
    <div>
      {confirmDialog}
      <Link to="/jobs" className="text-sm text-amber-800 hover:underline">
        ← Задания
      </Link>
      <div className="mb-4 mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">
          Задание №{job.number}: «{job.recipe_name}», {job.quantity}&nbsp;шт
        </h1>
        <Badge status={job.status}>{job.status_label}</Badge>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {job.status === 'draft' && manage && (
          <Button variant="primary" disabled={action.busy} onClick={() => act('plan')}>
            Запланировать и зарезервировать
          </Button>
        )}
        {job.status === 'planned' && manage && job.has_deficit && (
          <Button disabled={action.busy} onClick={() => act('reserve')}>
            Дозарезервировать
          </Button>
        )}
        {job.status === 'planned' && runJobs && (
          <Button
            variant="primary"
            disabled={action.busy || job.has_deficit}
            title={job.has_deficit ? 'Запустить задание с дефицитом нельзя' : undefined}
            onClick={() => act('start')}
          >
            Запустить
          </Button>
        )}
        {job.status === 'in_progress' && runJobs && !completing && (
          <Button variant="primary" onClick={() => setCompleting(true)}>
            Завершить…
          </Button>
        )}
        {['draft', 'planned'].includes(job.status) && manage && (
          <Button variant="danger" disabled={action.busy} onClick={() => act('cancel', {
                title: `Отменить задание №${job.number}?`,
                message: job.reservations.length
                  ? 'Резервы материалов будут сняты и вернутся в свободный остаток. Отменённое задание нельзя вернуть.'
                  : 'Отменённое задание нельзя вернуть.',
                confirmText: 'Отменить задание',
                cancelText: 'Не отменять',
                danger: true,
              })}>
            Отменить
          </Button>
        )}
      </div>
      <ErrorBanner message={action.error} />
      {job.status === 'planned' && job.has_deficit && (
        <p className="mb-4 rounded-md bg-yellow-50 px-4 py-2 text-sm text-yellow-900">
          Не хватает материалов — запустить задание нельзя.
          {manage
            ? ' Когда новая партия будет принята, нажмите «Дозарезервировать».'
            : ' Задание можно будет запустить после поступления и приёмки материалов.'}
        </p>
      )}

      {completing && (
        <CompleteForm
          job={job}
          onCancel={() => setCompleting(false)}
          onDone={(result) => {
            setCompleting(false)
            setData(result)
            history.reload()
          }}
        />
      )}

      <Card title="Компоненты">
        <div className="overflow-x-auto">
          <table className="min-w-full text-sm">
            <thead className="text-left text-xs uppercase text-stone-500">
              <tr>
                <th className="py-1 pr-4">Материал</th>
                <th className="py-1 pr-4 text-right">По норме</th>
                {active && <th className="py-1 pr-4 text-right">В резерве</th>}
                {active && <th className="py-1 pr-4 text-right">Дефицит</th>}
                <th className="py-1 pr-4 text-right">Факт</th>
                {active && <th className="py-1">Резерв</th>}
              </tr>
            </thead>
            <tbody>
              {job.materials.map((m) => (
                <tr key={m.material_id} className="border-t border-stone-100">
                  <td className="py-2 pr-4">
                    {m.material_name} <span className="text-xs text-stone-400">{m.material_code}</span>
                  </td>
                  <td className="whitespace-nowrap py-2 pr-4 text-right">{fmtQty(m.required_qty, m.unit)}</td>
                  {active && <td className="whitespace-nowrap py-2 pr-4 text-right">{fmtQty(m.reserved_qty, m.unit)}</td>}
                  {active && (
                    <td className={`whitespace-nowrap py-2 pr-4 text-right ${isPositive(m.deficit_qty) ? 'font-medium text-red-700' : 'text-stone-400'}`}>
                      {isPositive(m.deficit_qty) ? fmtQty(m.deficit_qty, m.unit) : '—'}
                    </td>
                  )}
                  <td className="whitespace-nowrap py-2 pr-4 text-right">{fmtQty(m.actual_qty, m.unit)}</td>
                  {active && (
                    <td className="py-2">
                      <TrafficLight light={m.light} />
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Сведения">
          <dl className="space-y-1.5 text-sm">
            <div className="flex justify-between"><dt className="text-stone-500">Дата задания</dt><dd>{fmtDate(job.planned_on)}</dd></div>
            <div className="flex justify-between"><dt className="text-stone-500">Создано</dt><dd>{fmtDateTime(job.created_at)}</dd></div>
            {job.started_at && <div className="flex justify-between"><dt className="text-stone-500">Запущено</dt><dd>{fmtDateTime(job.started_at)}</dd></div>}
            {job.completed_at && <div className="flex justify-between"><dt className="text-stone-500">Завершено</dt><dd>{fmtDateTime(job.completed_at)}</dd></div>}
            {job.cancelled_at && <div className="flex justify-between"><dt className="text-stone-500">Отменено</dt><dd>{fmtDateTime(job.cancelled_at)}</dd></div>}
            <div className="flex justify-between">
              <dt className="text-stone-500">{job.cost_is_final ? 'Себестоимость (факт)' : 'Себестоимость (оценка по резерву)'}</dt>
              <dd className="font-medium">
                {fmtMoney(job.cost_rub)}
                {!job.cost_covers_all && <span className="block text-xs font-normal text-red-700">без учёта дефицита</span>}
              </dd>
            </div>
            {job.comment && <div className="pt-2 text-stone-600">{job.comment}</div>}
          </dl>
        </Card>
        <Card title="Зарезервированные партии (FIFO)">
          {job.reservations.length === 0 ? (
            <p className="text-sm text-stone-500">Резервов нет</p>
          ) : (
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase text-stone-500">
                <tr>
                  <th className="py-1 pr-4">Партия</th>
                  <th className="py-1 pr-4">Материал</th>
                  <th className="py-1 pr-4">Поступила</th>
                  <th className="py-1 text-right">Количество</th>
                </tr>
              </thead>
              <tbody>
                {job.reservations.map((r) => {
                  const m = job.materials.find((x) => x.material_id === r.material_id)
                  return (
                    <tr key={r.batch_id} className="border-t border-stone-100">
                      <td className="py-1.5 pr-4">
                        <Link to={`/batches/${r.batch_id}`} className="text-amber-800 hover:underline">
                          №{r.batch_number}
                        </Link>
                      </td>
                      <td className="py-1.5 pr-4">{m?.material_name}</td>
                      <td className="py-1.5 pr-4">{fmtDate(r.received_on)}</td>
                      <td className="whitespace-nowrap py-1.5 text-right">{fmtQty(r.quantity, m?.unit)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      {['completed', 'marked'].includes(job.status) && (
        <MarkingCard
          jobId={job.id}
          onDelivered={() => {
            reload()
            history.reload()
          }}
        />
      )}

      <Card title="Журнал изменений">
        {history.data ? <AuditTable entries={history.data} /> : <ErrorBanner message={history.error} />}
      </Card>
    </div>
  )
}

