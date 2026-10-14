import { getToken } from './api'

export type ChatFileData = {
  id: number
  filename: string
  content_type: string
  size: number
  origin: 'upload' | 'generated'
  url: string
}

export type CodeRun = { code: string; logs: string; error: boolean; images: number }

// Файлы чата отдаются только с токеном, поэтому <img src> и обычная ссылка не подходят:
// забираем содержимое через fetch и показываем через blob-URL.
export async function fetchBlob(url: string): Promise<Blob> {
  const response = await fetch(url, { headers: { Authorization: `Bearer ${getToken() ?? ''}` } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.blob()
}

export function isChatFileUrl(url: string | undefined): url is string {
  return !!url && url.startsWith('/api/chat/files/')
}

export async function downloadChatFile(url: string, filename: string) {
  const blob = await fetchBlob(url)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  setTimeout(() => URL.revokeObjectURL(link.href), 1000)
}


// Картинки, вставленные в ответ как картинки (![…](url)). Обычная ссылка [скачать](url)
// картинку не показывает — такой график надо показать под ответом.
export function imagesInText(content: string, files: ChatFileData[]): Set<string> {
  return new Set(files.filter((f) => isImageEmbed(content, f.url)).map((f) => f.url))
}

function isImageEmbed(content: string, url: string): boolean {
  const target = `](${url})`
  for (let at = content.indexOf(target); at !== -1; at = content.indexOf(target, at + 1)) {
    const open = content.lastIndexOf('[', at)
    if (open > 0 && content[open - 1] === '!') return true
  }
  return false
}

// Те же правила, что на бэкенде: CSV или XLSX до 5 МБ. Проверяем до отправки, чтобы не
// гонять большой файл по сети ради отказа
export const MAX_UPLOAD_BYTES = 5 * 1024 * 1024

export function uploadProblem(file: { name: string; size: number }): string | null {
  if (!/\.(csv|xlsx)$/i.test(file.name)) return 'Можно загрузить только таблицу CSV или Excel (.xlsx)'
  if (file.size === 0) return 'Файл пустой'
  if (file.size > MAX_UPLOAD_BYTES) return 'Файл больше 5 МБ'
  return null
}

export function uploadErrorText(status: number, body: { detail?: unknown }): string {
  if (typeof body.detail === 'string') return body.detail
  if (status === 413) return 'Файл больше 5 МБ'
  return 'Не удалось загрузить файл'
}
