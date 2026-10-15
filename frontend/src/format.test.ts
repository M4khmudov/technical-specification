import { describe, expect, it } from 'vitest'
import { auditKey, auditText, todayIso } from './format'

describe('todayIso (S1)', () => {
  it('дата — по Москве, а не по часам компьютера', () => {
    // 18.10 22:30 UTC — в Москве уже 19.10, 01:30
    expect(todayIso(new Date(Date.UTC(2026, 9, 18, 22, 30)))).toBe('2026-10-19')
  })
})

describe('журнал по-человечески (M7)', () => {
  it('статусы, числа и даты — читаемые', () => {
    expect(auditKey('status')).toBe('Статус')
    expect(auditText('status', 'planned')).toBe('Запланировано')
    expect(auditText('quantity', '1000.000')).toBe('1\u00a0000')
    expect(auditText('quantity', '12345678901234567.5')).toBe('12\u00a0345\u00a0678\u00a0901\u00a0234\u00a0567,5') // без потери точности
    expect(auditText('planned_on', '2026-10-16')).toBe('16.10.2026')
    expect(auditText('comment', 'Брак упаковки')).toBe('Брак упаковки')
  })
})
