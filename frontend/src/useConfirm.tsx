import { useCallback, useState } from 'react'
import type { ReactNode } from 'react'
import ConfirmDialog from './components/ConfirmDialog'
import type { ConfirmOptions } from './components/ConfirmDialog'

type Pending = ConfirmOptions & { resolve: (ok: boolean) => void }

// Подтверждение в стиле приложения вместо системного window.confirm.
//   const [confirm, dialog] = useConfirm()
//   if (!(await confirm({ title: 'Удалить диалог?', danger: true }))) return
//   ... и {dialog} в разметке компонента.
export function useConfirm(): [(options: ConfirmOptions) => Promise<boolean>, ReactNode] {
  const [pending, setPending] = useState<Pending | null>(null)

  const confirm = useCallback(
    (options: ConfirmOptions) => new Promise<boolean>((resolve) => setPending({ ...options, resolve })),
    [],
  )

  const close = useCallback(
    (ok: boolean) => {
      pending?.resolve(ok)
      setPending(null)
    },
    [pending],
  )

  return [confirm, pending ? <ConfirmDialog options={pending} onClose={close} /> : null]
}
