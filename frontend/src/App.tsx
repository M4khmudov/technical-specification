import { useEffect, useState } from 'react'

type Health = { status: string; database: string }

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setHealth)
      .catch((e: Error) => setError(e.message))
  }, [])

  return (
    <main className="min-h-screen bg-stone-50 p-8 text-stone-900">
      <h1 className="text-2xl font-semibold">Свечная мастерская</h1>
      <p className="mt-4 text-sm">
        Бэкенд:{' '}
        {health ? (
          <span className="text-green-700">{health.status}</span>
        ) : error ? (
          <span className="text-red-700">недоступен ({error})</span>
        ) : (
          'проверяем…'
        )}
      </p>
    </main>
  )
}
