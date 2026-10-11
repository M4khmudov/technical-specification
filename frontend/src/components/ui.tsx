import type { ButtonHTMLAttributes, ReactNode, Ref } from 'react'
import type { Light } from '../api'

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div role="alert" className="my-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      {message}
    </div>
  )
}

export function Loading() {
  return <p className="py-8 text-center text-sm text-stone-500">Загрузка…</p>
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'dangerSolid'
  ref?: Ref<HTMLButtonElement>
}

export function Button({ variant = 'secondary', className = '', ...props }: ButtonProps) {
  const styles = {
    primary: 'bg-amber-700 text-white hover:bg-amber-800 border-amber-700',
    secondary: 'bg-white text-stone-800 hover:bg-stone-100 border-stone-300',
    danger: 'bg-white text-red-700 hover:bg-red-50 border-red-300',
    dangerSolid: 'bg-red-700 text-white hover:bg-red-800 border-red-700',
  }[variant]
  return (
    <button
      className={`rounded-md border px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
      {...props}
    />
  )
}

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="mb-6 rounded-lg border border-stone-200 bg-white">
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-200 px-4 py-3">
          <h2 className="font-semibold">{title}</h2>
          <div className="flex flex-wrap gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

const BADGE: Record<string, string> = {
  pending: 'bg-sky-100 text-sky-800',
  accepted: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
  draft: 'bg-stone-200 text-stone-700',
  planned: 'bg-sky-100 text-sky-800',
  in_progress: 'bg-amber-100 text-amber-800',
  completed: 'bg-green-100 text-green-800',
  marked: 'bg-emerald-200 text-emerald-900',
  cancelled: 'bg-stone-100 text-stone-500 line-through',
  sent: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
}

export function Badge({ status, children }: { status: string; children: ReactNode }) {
  return (
    <span className={`inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${BADGE[status] ?? 'bg-stone-100'}`}>
      {children}
    </span>
  )
}

const LIGHT = {
  green: { color: 'bg-green-500', label: 'Всё в резерве' },
  yellow: { color: 'bg-yellow-400', label: 'Зарезервирована часть' },
  red: { color: 'bg-red-500', label: 'Ничего не зарезервировано' },
}

export function TrafficLight({ light }: { light: Light | null }) {
  if (!light) return <span className="text-stone-400">—</span>
  const { color, label } = LIGHT[light]
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap">
      <span className={`inline-block h-3 w-3 rounded-full ${color}`} aria-hidden />
      <span className="text-xs text-stone-600">{label}</span>
    </span>
  )
}

export function Pagination({
  page,
  size,
  total,
  onPage,
}: {
  page: number
  size: number
  total: number
  onPage: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="mt-3 flex items-center justify-between text-sm text-stone-600">
      <span>
        Всего: {total}. Страница {page} из {pages}
      </span>
      <div className="flex gap-2">
        <Button disabled={page <= 1} onClick={() => onPage(page - 1)}>
          ← Назад
        </Button>
        <Button disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Вперёд →
        </Button>
      </div>
    </div>
  )
}

export const inputClass =
  'w-full rounded-md border border-stone-300 bg-white px-2 py-1.5 text-sm focus:border-amber-600 focus:outline-none'

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-stone-600">{label}</span>
      {children}
    </label>
  )
}
