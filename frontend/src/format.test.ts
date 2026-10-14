import { describe, expect, it } from 'vitest'
import { todayIso } from './format'

describe('todayIso (S1)', () => {
  it('дата — по Москве, а не по часам компьютера', () => {
    // 18.10 22:30 UTC — в Москве уже 19.10, 01:30
    expect(todayIso(new Date(Date.UTC(2026, 9, 18, 22, 30)))).toBe('2026-10-19')
  })
})
