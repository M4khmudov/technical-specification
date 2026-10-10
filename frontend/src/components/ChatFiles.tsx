import { useEffect, useState } from 'react'
import { downloadChatFile, fetchBlob } from '../chatFiles'
import type { ChatFileData, CodeRun } from '../chatFiles'

export function AuthImage({ src, alt }: { src: string; alt?: string }) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let revoked = false
    let created: string | null = null
    fetchBlob(src)
      .then((blob) => {
        if (revoked) return
        created = URL.createObjectURL(blob)
        setBlobUrl(created)
      })
      .catch(() => setFailed(true))
    return () => {
      revoked = true
      if (created) URL.revokeObjectURL(created)
    }
  }, [src])
  if (failed) return <span className="text-xs text-red-700">Не удалось загрузить изображение</span>
  if (!blobUrl) return <span className="text-xs text-stone-400">Загрузка изображения…</span>
  return <img src={blobUrl} alt={alt ?? ''} className="my-2 max-h-96 max-w-full rounded border border-stone-200" />
}

function sizeLabel(bytes: number) {
  return bytes < 1024 ? `${bytes} Б` : bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} КБ` : `${(bytes / 1024 / 1024).toFixed(1)} МБ`
}

export function FileChip({ file }: { file: ChatFileData }) {
  const [error, setError] = useState(false)
  return (
    <button
      type="button"
      onClick={() => downloadChatFile(file.url, file.filename).catch(() => setError(true))}
      className="inline-flex items-center gap-1 rounded border border-stone-300 bg-white px-2 py-1 text-xs hover:bg-stone-50"
      title="Скачать"
    >
      📎 {file.filename} <span className="text-stone-400">{sizeLabel(file.size)}</span>
      {error && <span className="text-red-700">— не удалось скачать</span>}
    </button>
  )
}

// Файлы, созданные кодом: картинки показываем сразу, остальное — кнопкой «скачать»
export function GeneratedFiles({ files, shownInText }: { files: ChatFileData[]; shownInText: Set<string> }) {
  if (files.length === 0) return null
  const images = files.filter((f) => f.content_type.startsWith('image/') && !shownInText.has(f.url))
  return (
    <div className="mt-2">
      {images.map((f) => (
        <AuthImage key={f.id} src={f.url} alt={f.filename} />
      ))}
      <div className="mt-1 flex flex-wrap gap-2">
        {files.map((f) => (
          <FileChip key={f.id} file={f} />
        ))}
      </div>
    </div>
  )
}

export function CodeRuns({ runs }: { runs: CodeRun[] }) {
  const [open, setOpen] = useState(false)
  if (runs.length === 0) return null
  const failed = runs.some((r) => r.error)
  return (
    <div className="mt-2">
      <button className="text-xs text-stone-500 hover:text-stone-800" onClick={() => setOpen(!open)}>
        {'</>'} {open ? 'Скрыть код' : `Показать код (${runs.length})`}
        {failed && <span className="ml-1 text-red-700">· выполнение завершилось с ошибкой</span>}
      </button>
      {open &&
        runs.map((run, i) => (
          <div key={i} className="mt-1 overflow-hidden rounded border border-stone-200 text-xs">
            <pre className="max-h-80 overflow-auto bg-stone-900 p-2 text-stone-100">
              <code>{run.code}</code>
            </pre>
            {run.logs && (
              <pre className={`max-h-40 overflow-auto p-2 ${run.error ? 'bg-red-50 text-red-900' : 'bg-stone-50 text-stone-700'}`}>{run.logs}</pre>
            )}
          </div>
        ))}
    </div>
  )
}
