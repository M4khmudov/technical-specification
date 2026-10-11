import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button } from './ui'

export type ConfirmOptions = {
  title: string
  message?: ReactNode
  confirmText?: string
  cancelText?: string
  danger?: boolean // разрушительное действие — красная кнопка
}

export default function ConfirmDialog({ options, onClose }: { options: ConfirmOptions; onClose: (ok: boolean) => void }) {
  const cancelRef = useRef<HTMLButtonElement | null>(null)
  const confirmRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    cancelRef.current?.focus() // по умолчанию — безопасный вариант
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose(false)
      if (e.key === 'Tab') {
        // фокус не уходит из окна
        const order = [cancelRef.current, confirmRef.current]
        const index = order.indexOf(document.activeElement as HTMLButtonElement)
        e.preventDefault()
        order[(index + (e.shiftKey ? order.length - 1 : 1)) % order.length]?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-stone-900/40 p-4 backdrop-blur-[1px] animate-[fade-in_120ms_ease-out]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose(false)}
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby={options.message ? 'confirm-message' : undefined}
        className="w-full max-w-sm rounded-lg border border-stone-200 bg-white p-5 shadow-xl animate-[pop-in_140ms_ease-out]"
      >
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
              options.danger ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800'
            }`}
          >
            {options.danger ? '!' : '?'}
          </span>
          <div>
            <h2 id="confirm-title" className="font-semibold text-stone-900">
              {options.title}
            </h2>
            {options.message && (
              <div id="confirm-message" className="mt-1 text-sm text-stone-600">
                {options.message}
              </div>
            )}
          </div>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button ref={cancelRef} onClick={() => onClose(false)}>
            {options.cancelText ?? 'Отмена'}
          </Button>
          <Button
            ref={confirmRef}
            variant={options.danger ? 'dangerSolid' : 'primary'}
            onClick={() => onClose(true)}
          >
            {options.confirmText ?? 'Подтвердить'}
          </Button>
        </div>
      </div>
    </div>
  )
}
