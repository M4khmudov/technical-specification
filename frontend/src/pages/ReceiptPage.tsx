import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import type { Currency, Material, Supplier } from '../api'
import { Badge, Button, Card, ErrorBanner, Field, inputClass } from '../components/ui'
import { BATCH_STATUS, fmtMoney, fmtQty, todayIso } from '../format'
import { useAction, useApi } from '../useApi'

type Line = { material_id: string; quantity: string; price: string; currency: Currency; comment: string }
type ReceiptResult = {
  id: number
  batches: { id: number; number: number; material_name: string; unit: string; quantity: string; unit_cost_rub: string; status: string }[]
}

const emptyLine = (): Line => ({ material_id: '', quantity: '', price: '', currency: 'RUB', comment: '' })

export default function ReceiptPage() {
  const { data: materials } = useApi<Material[]>('/materials')
  const { data: suppliers } = useApi<Supplier[]>('/suppliers')
  const [supplierId, setSupplierId] = useState('')
  const [receivedOn, setReceivedOn] = useState(todayIso())
  const [lines, setLines] = useState<Line[]>([emptyLine()])
  const [result, setResult] = useState<ReceiptResult | null>(null)
  const { busy, error, run } = useAction()

  const update = (i: number, patch: Partial<Line>) =>
    setLines(lines.map((line, j) => (i === j ? { ...line, ...patch } : line)))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const created = await run(() =>
      api<ReceiptResult>('/receipts', {
        method: 'POST',
        body: {
          supplier_id: Number(supplierId),
          received_on: receivedOn,
          lines: lines.map((l) => ({
            material_id: Number(l.material_id),
            quantity: l.quantity.replace(',', '.'),
            price: l.price.replace(',', '.'),
            currency: l.currency,
            comment: l.comment || null,
          })),
        },
      }),
    )
    if (created) {
      setResult(created)
      setLines([emptyLine()])
    }
  }

  return (
    <div>
      <h1 className="mb-4 text-2xl font-semibold">Поступление от поставщика</h1>
      {result && (
        <Card title={`Поступление #${result.id} оформлено`}>
          <p className="mb-2 text-sm text-stone-600">Каждая строка стала партией «На проверке». Примите партии в карточке.</p>
          <ul className="space-y-1 text-sm">
            {result.batches.map((b) => (
              <li key={b.id} className="flex flex-wrap items-center gap-2">
                <Link to={`/batches/${b.id}`} className="font-medium text-amber-800 hover:underline">
                  Партия №{b.number}
                </Link>
                {b.material_name}, {fmtQty(b.quantity, b.unit)}, себестоимость {fmtMoney(b.unit_cost_rub)}/ед.
                <Badge status={b.status}>{BATCH_STATUS[b.status]}</Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card>
        <form onSubmit={submit}>
          <div className="mb-4 grid gap-3 sm:grid-cols-3">
            <Field label="Поставщик">
              <select className={inputClass} value={supplierId} onChange={(e) => setSupplierId(e.target.value)} required>
                <option value="">Выберите…</option>
                {suppliers?.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Дата поступления">
              <input
                type="date"
                max={todayIso()}
                className={inputClass}
                value={receivedOn}
                onChange={(e) => setReceivedOn(e.target.value)}
                required
              />
            </Field>
          </div>
          <p className="mb-2 text-sm text-stone-600">
            Себестоимость в рублях считается по курсу на дату поступления (в выходной — по последнему предыдущему курсу).
          </p>
          <div className="space-y-2">
            {lines.map((line, i) => (
              <div key={i} className="grid items-end gap-2 rounded-md bg-stone-50 p-2 sm:grid-cols-12">
                <div className="sm:col-span-3">
                  <Field label="Материал">
                    <select className={inputClass} value={line.material_id} onChange={(e) => update(i, { material_id: e.target.value })} required>
                      <option value="">Выберите…</option>
                      {materials?.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.name} ({m.unit})
                        </option>
                      ))}
                    </select>
                  </Field>
                </div>
                <div className="sm:col-span-2">
                  <Field label="Количество">
                    <input className={inputClass} inputMode="decimal" value={line.quantity} onChange={(e) => update(i, { quantity: e.target.value })} required />
                  </Field>
                </div>
                <div className="sm:col-span-2">
                  <Field label="Цена за ед.">
                    <input className={inputClass} inputMode="decimal" value={line.price} onChange={(e) => update(i, { price: e.target.value })} required />
                  </Field>
                </div>
                <div className="sm:col-span-1">
                  <Field label="Валюта">
                    <select className={inputClass} value={line.currency} onChange={(e) => update(i, { currency: e.target.value as Currency })}>
                      <option>RUB</option>
                      <option>USD</option>
                      <option>EUR</option>
                    </select>
                  </Field>
                </div>
                <div className="sm:col-span-3">
                  <Field label="Комментарий">
                    <input className={inputClass} value={line.comment} onChange={(e) => update(i, { comment: e.target.value })} />
                  </Field>
                </div>
                <div className="sm:col-span-1">
                  <Button type="button" disabled={lines.length === 1} onClick={() => setLines(lines.filter((_, j) => j !== i))} title="Удалить строку">
                    ✕
                  </Button>
                </div>
              </div>
            ))}
          </div>
          <ErrorBanner message={error} />
          <div className="mt-3 flex gap-2">
            <Button type="button" onClick={() => setLines([...lines, emptyLine()])}>
              + Строка
            </Button>
            <Button variant="primary" type="submit" disabled={busy}>
              Оформить поступление
            </Button>
          </div>
        </form>
      </Card>
    </div>
  )
}
