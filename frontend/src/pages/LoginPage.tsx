import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useAuth } from '../auth'
import { Button, ErrorBanner, Field, inputClass } from '../components/ui'
import { useAction } from '../useApi'

export default function LoginPage() {
  const { user, login } = useAuth()
  const location = useLocation()
  const [loginName, setLoginName] = useState('')
  const [password, setPassword] = useState('')
  const { busy, error, run } = useAction()

  if (user) {
    const from = (location.state as { from?: string } | null)?.from ?? '/batches'
    return <Navigate to={from} replace />
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    void run(() => login(loginName, password))
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-stone-50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-lg border border-stone-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-semibold">🕯️ Свечная мастерская</h1>
        <p className="mb-5 text-sm text-stone-500">Учёт склада и производства</p>
        <div className="space-y-3">
          <Field label="Логин">
            <input className={inputClass} value={loginName} onChange={(e) => setLoginName(e.target.value)} autoFocus required />
          </Field>
          <Field label="Пароль">
            <input
              type="password"
              className={inputClass}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </Field>
        </div>
        <ErrorBanner message={error} />
        <Button variant="primary" type="submit" disabled={busy} className="mt-4 w-full">
          {busy ? 'Входим…' : 'Войти'}
        </Button>
        <p className="mt-4 text-xs text-stone-500">Демо: sklad, tech, boss — пароль demo1234</p>
      </form>
    </div>
  )
}
