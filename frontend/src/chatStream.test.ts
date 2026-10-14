import { describe, expect, it } from 'vitest'
import { settleUnfinished } from './chatStream'

describe('поток без финального события (M10)', () => {
  it('ответ «Думаю…» становится оборванным', () => {
    const m = { role: 'assistant', status: 'streaming', status_label: 'Отвечает…', content: '' }
    expect(settleUnfinished(m)).toMatchObject({ status: 'interrupted', status_label: 'Ответ оборван' })
  })
  it('завершённый ответ не трогаем', () => {
    const m = { role: 'assistant', status: 'complete', status_label: 'Готово' }
    expect(settleUnfinished(m)).toBe(m)
  })
})
