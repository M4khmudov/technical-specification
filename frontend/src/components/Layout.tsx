import { NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Layout() {
  const { user, logout, can } = useAuth()
  const link = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-3 py-1.5 text-sm ${isActive ? 'bg-amber-100 text-amber-900' : 'text-stone-700 hover:bg-stone-100'}`

  return (
    <div className="min-h-screen bg-stone-50 text-stone-900">
      <header className="border-b border-stone-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-3">
          <span className="mr-4 font-semibold">🕯️ Свечная мастерская</span>
          <nav className="flex flex-wrap gap-1">
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
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-stone-600">
              {user?.full_name} · {user?.role_label}
            </span>
            <button onClick={logout} className="text-amber-800 hover:underline">
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
