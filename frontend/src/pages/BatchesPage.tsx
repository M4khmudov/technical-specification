import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { query } from '../api'
import type { Batch, Page } from '../api'
import { Badge, ErrorBanner, inputClass, Pagination } from '../components/ui'
import { BATCH_STATUS, fmtDate, fmtMoney, fmtQty } from '../format'
import { useApi } from '../useApi'

// Фильтры, сортировка и страница живут в адресной строке:
// ссылкой на отфильтрованный реестр можно поделиться, «Назад» работает.
const FILTERS = ['number', 'received_from', 'received_to', 'material', 'supplier', 'currency', 'status'] as const

type Column = {
  key: string
  title: string
  sort?: string
  align?: 'right'
  filter?: React.ReactNode
}

function TextFilter({ name, placeholder, type = 'text' }: { name: string; placeholder?: string; type?: string }) {
  const [params, setParams] = useSearchParams()
  const urlValue = params.get(name) ?? ''
  const [value, setValue] = useState(urlValue)

  // Если адрес поменялся извне (Назад/сброс) — обновляем поле
  useEffect(() => setValue(urlValue), [urlValue])

  // Задержка, чтобы не запрашивать сервер на каждую букву
  useEffect(() => {
    if (value === urlValue) return
    const timer = setTimeout(() => {
      setParams((prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(name, value)
        else next.delete(name)
        next.delete('page')
        return next
      })
    }, 400)
    return () => clearTimeout(timer)
  }, [value, urlValue, name, setParams])

  return (
    <input
      type={type}
      className={inputClass}
      placeholder={placeholder}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      aria-label={placeholder ?? name}
    />
  )
}

function SelectFilter({ name, options }: { name: string; options: Record<string, string> }) {
  const [params, setParams] = useSearchParams()
  return (
    <select
      className={inputClass}
      value={params.get(name) ?? ''}
      aria-label={name}
      onChange={(e) =>
        setParams((prev) => {
          const next = new URLSearchParams(prev)
          if (e.target.value) next.set(name, e.target.value)
          else next.delete(name)
          next.delete('page')
          return next
        })
      }
    >
      <option value="">Все</option>
      {Object.entries(options).map(([value, label]) => (
        <option key={value} value={value}>
          {label}
        </option>
      ))}
    </select>
  )
}

export default function BatchesPage() {
  const [params, setParams] = useSearchParams()
  const sort = params.get('sort') ?? 'number'
  const order = params.get('order') ?? 'desc'
  const page = Number(params.get('page') ?? '1')
  const size = Number(params.get('size') ?? '20')

  const path = `/batches${query({
    ...Object.fromEntries(FILTERS.map((f) => [f, params.get(f)])),
    sort,
    order,
    page,
    size,
  })}`
  const { data, error, loading } = useApi<Page<Batch>>(path)

  const setSort = (key: string) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      const sameColumn = (prev.get('sort') ?? 'number') === key
      next.set('sort', key)
      next.set('order', sameColumn && (prev.get('order') ?? 'desc') === 'desc' ? 'asc' : 'desc')
      next.delete('page')
      return next
    })

  const setPage = (p: number) =>
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('page', String(p))
      return next
    })

  const columns: Column[] = [
    { key: 'number', title: '№', sort: 'number', filter: <TextFilter name="number" placeholder="№" type="number" /> },
    {
      key: 'received_on',
      title: 'Дата',
      sort: 'received_on',
      filter: (
        <div className="flex flex-col gap-1">
          <TextFilter name="received_from" placeholder="с" type="date" />
          <TextFilter name="received_to" placeholder="по" type="date" />
        </div>
      ),
    },
    { key: 'material', title: 'Материал', sort: 'material', filter: <TextFilter name="material" placeholder="Код или название" /> },
    { key: 'supplier', title: 'Поставщик', sort: 'supplier', filter: <TextFilter name="supplier" placeholder="Поставщик" /> },
    { key: 'quantity', title: 'Пришло', sort: 'quantity', align: 'right' },
    { key: 'free_qty', title: 'Свободно', sort: 'free_qty', align: 'right' },
    {
      key: 'price',
      title: 'Цена',
      sort: 'currency',
      align: 'right',
      filter: <SelectFilter name="currency" options={{ RUB: 'RUB', USD: 'USD', EUR: 'EUR' }} />,
    },
    { key: 'unit_cost_rub', title: 'Себест., ₽/ед.', sort: 'unit_cost_rub', align: 'right' },
    { key: 'status', title: 'Статус', sort: 'status', filter: <SelectFilter name="status" options={BATCH_STATUS} /> },
  ]

  const hasFilters = FILTERS.some((f) => params.get(f))

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold">Реестр партий</h1>
        {hasFilters && (
          <button className="text-sm text-amber-800 hover:underline" onClick={() => setParams({})}>
            Сбросить фильтры
          </button>
        )}
      </div>
      <ErrorBanner message={error} />
      <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
        <table className="min-w-full text-sm">
          <thead className="bg-stone-100 text-left text-xs uppercase tracking-wide text-stone-600">
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={`px-3 py-2 ${c.align === 'right' ? 'text-right' : ''}`}>
                  {c.sort ? (
                    <button
                      className="inline-flex items-center gap-1 uppercase hover:text-stone-900"
                      onClick={() => setSort(c.sort!)}
                      aria-label={`Сортировать по колонке «${c.title}»`}
                    >
                      {c.title}
                      <span className="text-amber-700">{sort === c.sort ? (order === 'asc' ? '▲' : '▼') : ''}</span>
                    </button>
                  ) : (
                    c.title
                  )}
                </th>
              ))}
            </tr>
            <tr className="bg-stone-50 normal-case">
              {columns.map((c) => (
                <th key={c.key} className="px-2 pb-2 font-normal">
                  {c.filter}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className={loading ? 'opacity-50' : ''}>
            {data?.items.map((b) => (
              <tr key={b.id} className="border-t border-stone-100 hover:bg-amber-50/40">
                <td className="px-3 py-2">
                  <Link to={`/batches/${b.id}`} className="font-medium text-amber-800 hover:underline">
                    {b.number}
                  </Link>
                </td>
                <td className="whitespace-nowrap px-3 py-2">{fmtDate(b.received_on)}</td>
                <td className="px-3 py-2">
                  <div>{b.material_name}</div>
                  <div className="text-xs text-stone-500">{b.material_code}</div>
                </td>
                <td className="px-3 py-2">{b.supplier_name}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{fmtQty(b.quantity, b.unit)}</td>
                <td
                  className={`whitespace-nowrap px-3 py-2 text-right ${b.status === 'accepted' ? '' : 'text-stone-400'}`}
                  title={b.status === 'accepted' ? undefined : 'Использовать можно только принятые партии'}
                >
                  {fmtQty(b.free_qty, b.unit)}
                  {b.status !== 'accepted' && <span className="block text-xs">не доступно</span>}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{fmtMoney(b.price, b.currency)}</td>
                <td className="whitespace-nowrap px-3 py-2 text-right">{fmtMoney(b.unit_cost_rub)}</td>
                <td className="px-3 py-2">
                  <Badge status={b.status}>{BATCH_STATUS[b.status]}</Badge>
                </td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-3 py-8 text-center text-stone-500">
                  Партий не найдено
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {data && <Pagination page={data.page} size={data.size} total={data.total} onPage={setPage} />}
    </div>
  )
}
