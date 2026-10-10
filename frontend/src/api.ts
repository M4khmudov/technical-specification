// Клиент API. Все числа (количества, деньги) приходят строками — без потерь точности.

const TOKEN_KEY = 'candles.token'

export class ApiError extends Error {
  status: number
  errors?: string[]

  constructor(status: number, message: string, errors?: string[]) {
    super(message)
    this.status = status
    this.errors = errors
  }
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // приватный режим браузера — токен живёт только до перезагрузки
  }
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

export async function api<T>(
  path: string,
  options: { method?: string; body?: unknown; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(`/api${path}`, {
      method: options.method ?? 'GET',
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: options.signal,
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError(0, 'Сервер недоступен. Проверьте соединение и попробуйте ещё раз.')
  }

  if (response.status === 401 && path !== '/auth/login') onUnauthorized()
  if (!response.ok) {
    let message = `Ошибка ${response.status}`
    let errors: string[] | undefined
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      errors = body.errors
    } catch {
      // ответ не JSON — оставляем код
    }
    throw new ApiError(response.status, message, errors)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export function query(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

// ---------------------------------------------------------------- типы ответов

export type Permission =
  | 'receipt.create'
  | 'batch.review'
  | 'job.manage'
  | 'job.run'
  | 'audit.view'
  | 'marking.retry'

export type User = {
  id: number
  login: string
  full_name: string
  role: string
  role_label: string
  permissions: Permission[]
}

export type Page<T> = { items: T[]; total: number; page: number; size: number }

export type BatchStatus = 'pending' | 'accepted' | 'rejected'
export type Currency = 'RUB' | 'USD' | 'EUR'

export type Batch = {
  id: number
  number: number
  received_on: string
  material_id: number
  material_code: string
  material_name: string
  unit: string
  supplier_id: number
  supplier_name: string
  quantity: string
  remaining_qty: string
  reserved_qty: string
  free_qty: string
  price: string
  currency: Currency
  exchange_rate: string
  rate_date: string
  unit_cost_rub: string
  status: BatchStatus
  comment: string | null
}

export type AuditEntry = {
  action: string
  user_full_name: string
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
  created_at: string
}

export type Movement = {
  kind: 'receipt' | 'reserve' | 'release' | 'consume'
  quantity: string
  job_id: number | null
  job_number: number | null
  note: string | null
  user_full_name: string | null
  created_at: string
}

export type BatchDetail = Batch & { movements: Movement[]; audit: AuditEntry[] }

export type JobStatus = 'draft' | 'planned' | 'in_progress' | 'completed' | 'marked' | 'cancelled'
export type Light = 'green' | 'yellow' | 'red'

export type JobMaterial = {
  material_id: number
  material_code: string
  material_name: string
  unit: string
  required_qty: string
  reserved_qty: string
  deficit_qty: string
  actual_qty: string | null
  light: Light | null
}

export type Reservation = {
  batch_id: number
  batch_number: number
  material_id: number
  received_on: string
  quantity: string
  unit_cost_rub: string
}

export type Job = {
  id: number
  number: number
  recipe_id: number
  recipe_name: string
  quantity: number
  planned_on: string | null
  status: JobStatus
  status_label: string
  comment: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  has_deficit: boolean
  materials: JobMaterial[]
  reservations: Reservation[]
  cost_rub: string
  cost_is_final: boolean
  cost_covers_all: boolean
}

export type JobListItem = Pick<
  Job,
  'id' | 'number' | 'recipe_name' | 'quantity' | 'planned_on' | 'status' | 'status_label' | 'has_deficit'
>

export type Marking = {
  status: 'pending' | 'sent' | 'failed'
  status_label: string
  idempotency_key: string
  attempts_count: number
  next_attempt_at: string | null
  sent_at: string | null
  attempts: {
    attempt_no: number
    started_at: string
    duration_ms: number | null
    http_status: number | null
    error: string | null
  }[]
}

export type Material = { id: number; code: string; name: string; unit: string }
export type Supplier = { id: number; name: string }
export type Recipe = { id: number; name: string; items: { material_id: number; qty_per_unit: string }[] }

export type AuditListEntry = AuditEntry & {
  id: number
  entity_type: string
  entity_id: number
  user_id: number | null
}
