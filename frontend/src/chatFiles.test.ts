import { describe, expect, it } from 'vitest'
import { imagesInText, uploadErrorText, uploadProblem } from './chatFiles'
import type { ChatFileData } from './chatFiles'

const chart: ChatFileData = { id: 2, filename: 'chart.png', content_type: 'image/png', size: 10, origin: 'generated', url: '/api/chat/files/2' }

describe('imagesInText (B6)', () => {
  it('обычная ссылка на график не считается показанной картинкой', () => {
    expect(imagesInText('[Скачать график](/api/chat/files/2)', [chart]).size).toBe(0)
  })
  it('картинка в тексте показана — второй раз не нужна', () => {
    expect(imagesInText('![График](/api/chat/files/2)', [chart])).toEqual(new Set(['/api/chat/files/2']))
  })
  it('похожий адрес другого файла не совпадает', () => {
    expect(imagesInText('![x](/api/chat/files/22)', [chart]).size).toBe(0)
  })
})

describe('загрузка файлов (S6)', () => {
  it('большой файл отклоняется понятным текстом ещё до отправки', () => {
    expect(uploadProblem({ name: 'sales.xlsx', size: 6 * 1024 * 1024 })).toBe('Файл больше 5 МБ')
    expect(uploadProblem({ name: 'sales.xlsx', size: 2 * 1024 * 1024 })).toBeNull()
    expect(uploadProblem({ name: 'photo.png', size: 10 })).toMatch(/CSV или Excel/)
  })
  it('ответ 413 от прокси — тоже понятный текст', () => {
    expect(uploadErrorText(413, {})).toBe('Файл больше 5 МБ')
    expect(uploadErrorText(400, { detail: 'Файл пустой' })).toBe('Файл пустой')
  })
})
