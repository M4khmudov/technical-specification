import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import type { BatchDetail } from '../api'
import { useAuth } from '../auth'
import AuditTable from '../components/AuditTable'
import { Badge, Button, Card, ErrorBanner, inputClass, Loading } from '../components/ui'
import { BATCH_STATUS, fmtDate, fmtDateTime, fmtMoney, fmtQty, MOVEMENT_KIND } from '../format'
import { useAction, useApi } from '../useApi'

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-stone-100 py-1.5 text-sm last:border-0">
      <dt className="text-stone-500">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  )
}

export default function BatchPage() {
  const { id } = useParams()
  const { can } = useAuth()
  const { data: batch, error, loading, reload } = useApi<BatchDetail>(`/batches/${id}`)
  const action = useAction()
  const [rejecting, setRejecting] = useState(false)
  const [reason, setReason] = useState('')

  if (loading && !batch) return <Loading />
  if (!batch) return <ErrorBanner message={error} />

  const accept = () => action.run(() => api(`/batches/${batch.id}/accept`, { method: 'POST' })).then(reload)
  const reject = () =>
    action
      .run(() => api(`/batches/${batch.id}/reject`, { method: 'POST', body: { reason: reason || null } }))
      .then(() => {
        setRejecting(false)
        reload()
      })

  const canReview = can('batch.review') && batch.status === 'pending'

  return (
    <div>
      <Link to="/batches" className="text-sm text-amber-800 hover:underline">
        ← Реестр партий
      </Link>
      <div className="mb-4 mt-2 flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold">Партия №{batch.number}</h1>
        <Badge status={batch.status}>{BATCH_STATUS[batch.status]}</Badge>
      </div>
      <ErrorBanner message={action.error} />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          title="Партия"
          actions={
            canReview && (
              <>
                <Button variant="primary" disabled={action.busy} onClick={accept}>
                  Принять
                </Button>
                <Button variant="danger" disabled={action.busy} onClick={() => setRejecting(true)}>
                  Брак
                </Button>
              </>
            )
          }
        >
          {rejecting && (
            <div className="mb-4 rounded-md border border-red-200 bg-red-50 p-3">
              <input
                className={inputClass}
                placeholder="Причина брака (необязательно)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <div className="mt-2 flex gap-2">
                <Button variant="danger" disabled={action.busy} onClick={reject}>
                  Отправить в брак
                </Button>
                <Button onClick={() => setRejecting(false)}>Отмена</Button>
              </div>
            </div>
          )}
          <dl>
            <Row label="Материал">
              {batch.material_name} <span className="text-stone-500">({batch.material_code})</span>
            </Row>
            <Row label="Поставщик">{batch.supplier_name}</Row>
            <Row label="Дата поступления">{fmtDate(batch.received_on)}</Row>
            <Row label="Комментарий">{batch.comment ?? '—'}</Row>
          </dl>
        </Card>
        <Card title="Количество и стоимость">
          <dl>
            <Row label="Пришло">{fmtQty(batch.quantity, batch.unit)}</Row>
            <Row label="Остаток">{fmtQty(batch.remaining_qty, batch.unit)}</Row>
            <Row label="В резерве">{fmtQty(batch.reserved_qty, batch.unit)}</Row>
            <Row label="Свободно">
              <strong>{fmtQty(batch.free_qty, batch.unit)}</strong>
            </Row>
            <Row label="Цена">{fmtMoney(batch.price, batch.currency)} за ед.</Row>
            <Row label="Курс">
              {batch.currency === 'RUB' ? '—' : `${fmtQty(batch.exchange_rate)} ₽ на ${fmtDate(batch.rate_date)}`}
            </Row>
            <Row label="Себестоимость">{fmtMoney(batch.unit_cost_rub)} за ед.</Row>
          </dl>
        </Card>
      </div>

      <Card title="Движения по партии">
        {batch.movements.length === 0 ? (
          <p className="text-sm text-stone-500">Движений нет</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase text-stone-500">
                <tr>
                  <th className="py-1 pr-4">Когда</th>
                  <th className="py-1 pr-4">Движение</th>
                  <th className="py-1 pr-4 text-right">Количество</th>
                  <th className="py-1 pr-4">Задание</th>
                  <th className="py-1 pr-4">Кто</th>
                  <th className="py-1">Примечание</th>
                </tr>
              </thead>
              <tbody>
                {batch.movements.map((m, i) => (
                  <tr key={i} className="border-t border-stone-100">
                    <td className="whitespace-nowrap py-2 pr-4">{fmtDateTime(m.created_at)}</td>
                    <td className="py-2 pr-4">{MOVEMENT_KIND[m.kind]}</td>
                    <td className="whitespace-nowrap py-2 pr-4 text-right">{fmtQty(m.quantity, batch.unit)}</td>
                    <td className="py-2 pr-4">
                      {m.job_id ? (
                        <Link to={`/jobs/${m.job_id}`} className="text-amber-800 hover:underline">
                          №{m.job_number}
                        </Link>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td className="py-2 pr-4">{m.user_full_name ?? '—'}</td>
                    <td className="py-2">{m.note ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Журнал изменений">
        <AuditTable entries={batch.audit} />
      </Card>
    </div>
  )
}
