// Фактический расход при завершении: пустое поле — «по норме» (бэкенд подставит
// норму), а не 0. Раньше очищенное поле уходило как 0, и весь резерв молча
// становился «экономией».
export type ActualRow = { material_id: number; actual_qty: string }

export function actualsPayload(materialIds: number[], values: Record<number, string>): ActualRow[] {
  return materialIds
    .filter((id) => (values[id] ?? '').trim() !== '')
    .map((id) => ({ material_id: id, actual_qty: values[id].trim().replace(',', '.') }))
}
