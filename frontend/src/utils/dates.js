// Server timestamps are UTC. Older rows (and SQLite CURRENT_TIMESTAMP values)
// carry no offset, which browsers would otherwise interpret as local time.
export function parseServerDate(value) {
  if (!value) return null
  let s = String(value).replace(' ', 'T')
  if (!/([zZ]|[+-]\d\d:?\d\d)$/.test(s)) s += 'Z'
  const d = new Date(s)
  return isNaN(d) ? null : d
}

// Calendar dates like "2026-11-01" (check-back dates) are local, not UTC.
export function parseLocalDate(value) {
  if (!value) return null
  const [y, m, d] = String(value).slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  return new Date(y, m - 1, d)
}

export function formatDate(value, fallback = '—') {
  const d = parseServerDate(value)
  return d ? d.toLocaleDateString() : fallback
}

export function formatDateTime(value, options, fallback = '—') {
  const d = parseServerDate(value)
  return d ? d.toLocaleString(undefined, options) : fallback
}
