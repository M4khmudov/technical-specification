// Форматирование чисел из строк API без перевода во float: «1 250,5 кг», «1 250,00 ₽».

const NBSP = ' '

function group(integer: string): string {
  const sign = integer.startsWith('-') ? '-' : ''
  let digits = sign ? integer.slice(1) : integer
  const parts: string[] = []
  while (digits.length > 3) {
    parts.unshift(digits.slice(-3))
    digits = digits.slice(0, -3)
  }
  parts.unshift(digits || '0')
  return sign + parts.join(NBSP)
}

export function fmtQty(value: string | number | null | undefined, unit?: string): string {
  if (value === null || value === undefined || value === '') return '—'
  const [integer, fraction = ''] = String(value).split('.')
  const trimmed = fraction.replace(/0+$/, '')
  const text = group(integer) + (trimmed ? `,${trimmed}` : '')
  return unit ? `${text}${NBSP}${unit}` : text
}

export function fmtMoney(value: string | null | undefined, currency = '₽', places = 2): string {
  if (value === null || value === undefined || value === '') return '—'
  const [integer, fraction = ''] = value.split('.')
  // Обрезаем лишние знаки, не округляя через float; хвостовые нули сверх places убираем
  let frac = fraction.padEnd(places, '0')
  if (frac.length > places) frac = frac.replace(/0+$/, '').padEnd(places, '0')
  return `${group(integer)},${frac}${NBSP}${currency}`
}

// «Больше нуля» для десятичной строки без перевода во float
export function isPositive(value: string | null | undefined): boolean {
  return !!value && !value.startsWith('-') && /[1-9]/.test(value)
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return '—'
  const [y, m, d] = value.slice(0, 10).split('-')
  return `${d}.${m}.${y}`
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// Мастерская работает по Москве (как бэкенд, настройка TIMEZONE): дата по умолчанию в
// форме не должна зависеть от часового пояса компьютера пользователя
export const WORKSHOP_TIMEZONE = 'Europe/Moscow'

export function todayIso(now: Date = new Date()): string {
  // формат sv-SE — ГГГГ-ММ-ДД
  return new Intl.DateTimeFormat('sv-SE', { timeZone: WORKSHOP_TIMEZONE }).format(now)
}

export const BATCH_STATUS: Record<string, string> = {
  pending: 'На проверке',
  accepted: 'Принята',
  rejected: 'Брак',
}

export const MOVEMENT_KIND: Record<string, string> = {
  receipt: 'Поступление',
  reserve: 'Резерв',
  release: 'Снятие резерва',
  consume: 'Списание',
}

export const AUDIT_ACTION: Record<string, string> = {
  create: 'Создание',
  accept: 'Приёмка',
  reject: 'Брак',
  plan: 'Планирование',
  reserve: 'Дозарезервирование',
  start: 'Запуск',
  complete: 'Завершение',
  cancel: 'Отмена',
  marked: 'Принято сервисом маркировки',
  retry: 'Повтор отправки',
  remember: 'Ассистент запомнил',
  forget: 'Факт удалён из памяти',
}
