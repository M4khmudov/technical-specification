import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'
import { LogoutIcon } from './Icons'

// «Петров Иван Сергеевич» → «ПИ»
function initials(fullName?: string) {
  return (fullName ?? '')
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0] ?? '')
    .join('')
    .toUpperCase()
}

export default function Layout() {
  const { user, logout, can } = useAuth()
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-amber-100 text-amber-900' : 'text-stone-700 hover:bg-stone-100'}`

  return (
    <div className="min-h-screen bg-stone-50 text-stone-900">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
          <span className="mr-4 font-semibold">Свечная мастерская</span>
          <nav className="flex flex-wrap gap-1">
            <NavLink to="/assistant" className={link}>
              ✨ Ассистент
            </NavLink>
            <NavLink to="/batches" className={link}>
              Партии
            </NavLink>
            <NavLink to="/jobs" className={link}>
              Задания
            </NavLink>
            {can('receipt.create') && (
              <NavLink to="/receipts/new" className={link}>
                Поступление
              </NavLink>
            )}
            {can('audit.view') && (
              <NavLink to="/audit" className={link}>
                Журнал
              </NavLink>
            )}
          </nav>
          <div className="ml-auto flex items-center gap-4">
            <div className="flex items-center gap-3">
              <span
                aria-hidden
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-amber-100 text-sm font-semibold text-amber-900"
              >
                {initials(user?.full_name)}
              </span>
              <div className="leading-tight">
                <div className="text-sm font-medium text-stone-900">{user?.full_name}</div>
                <div className="text-xs text-stone-500">{user?.role_label}</div>
              </div>
            </div>
            <span aria-hidden className="h-8 w-px bg-stone-200" />
            <button
              onClick={logout}
              className="inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm text-stone-600 transition hover:bg-stone-100 hover:text-stone-900"
            >
              <LogoutIcon />
              Выйти
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}
