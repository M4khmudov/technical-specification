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

