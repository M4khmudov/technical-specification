import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from './api'

// Загрузка данных по пути API. path = null — ничего не загружать.
export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(path !== null)
  const [version, setVersion] = useState(0)

  useEffect(() => {
    if (path === null) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    api<T>(path, { signal: controller.signal })
      .then((result) => setData(result))
      .catch((e: unknown) => {
        if (e instanceof DOMException && e.name === 'AbortError') return
        setError(e instanceof ApiError ? e.message : 'Не удалось загрузить данные')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [path, version])

  const reload = useCallback(() => setVersion((v) => v + 1), [])
  return { data, setData, error, loading, reload }
}

// Выполнение действия (кнопки) с понятной ошибкой и флагом «выполняется».
export function useAction() {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = useCallback(async <T,>(action: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true)
    setError(null)
    try {
      return await action()
    } catch (e) {
      if (e instanceof ApiError) {
        setError(e.errors?.length ? `${e.message}` : e.message)
      } else {
        setError('Не удалось выполнить действие')
      }
      return undefined
    } finally {
      setBusy(false)
    }
  }, [])

  return { busy, error, setError, run }
}
