// Minimal RFC 4180 CSV parser: quoted fields, escaped quotes (""), commas and
// newlines inside quotes, CRLF line endings. Also accepts ; or tab delimiters.
export function parseCsv(text) {
  const src = text.replace(/^\uFEFF/, '') // strip BOM (Excel exports)
  const firstLine = src.split(/\r?\n/, 1)[0] || ''
  const delimiter = [',', ';', '\t'].reduce((best, d) =>
    firstLine.split(d).length > firstLine.split(best).length ? d : best, ',')

  const rows = []
  let row = []
  let field = ''
  let inQuotes = false

  for (let i = 0; i < src.length; i++) {
    const c = src[i]
    if (inQuotes) {
      if (c === '"' && src[i + 1] === '"') { field += '"'; i++ }
      else if (c === '"') inQuotes = false
      else field += c
    } else if (c === '"' && field === '') {
      inQuotes = true
    } else if (c === delimiter) {
      row.push(field); field = ''
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++
      row.push(field); field = ''
      if (row.some((v) => v.trim() !== '')) rows.push(row)
      row = []
    } else {
      field += c
    }
  }
  row.push(field)
  if (row.some((v) => v.trim() !== '')) rows.push(row)
  return rows
}

// Fields a CSV column can map to, with header names we recognise automatically
export const CSV_FIELDS = [
  { key: 'email', label: 'Email', required: true, guess: /e-?mail/i },
  { key: 'name', label: 'Name', guess: /^(full[ _]?)?name$|^contact|first[ _]?name/i },
  { key: 'company', label: 'Company', guess: /company|organi[sz]ation|employer|firm/i },
  { key: 'role', label: 'Role / Title', guess: /role|title|position|designation/i },
  { key: 'notes', label: 'Notes (personalisation)', guess: /note|hook|context|comment|about/i },
]

export function guessMapping(headers) {
  const mapping = {}
  for (const f of CSV_FIELDS) {
    const idx = headers.findIndex((h, i) => f.guess.test(h.trim()) && !Object.values(mapping).includes(i))
    mapping[f.key] = idx >= 0 ? idx : null
  }
  return mapping
}
