import { describe, expect, it } from 'vitest'
import { actualsPayload } from './completion'

describe('фактический расход (M2)', () => {
  it('очищенное поле — по норме, а не 0', () => {
    expect(actualsPayload([1, 2, 3], { 1: '12,5', 2: '', 3: '  ' })).toEqual([{ material_id: 1, actual_qty: '12.5' }])
  })
  it('явный ноль остаётся нулём', () => {
    expect(actualsPayload([1], { 1: '0' })).toEqual([{ material_id: 1, actual_qty: '0' }])
  })
})
