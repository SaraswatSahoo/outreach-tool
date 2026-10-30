import { useRef, useState } from 'react'
import api from '../api'
import { errorMessage } from '../context/UIContext'
import { CSV_FIELDS, guessMapping, parseCsv } from '../utils/csv'

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/

// Upload a CSV or Excel (.xlsx) file, map its columns to recipient fields, and hand back clean rows:
// onImport([{ email, name, company, role, notes }])
export default function CsvImport({ onImport }) {
  const inputRef = useRef(null)
  const [table, setTable] = useState(null) // { fileName, headers, rows }
  const [mapping, setMapping] = useState({})
  const [error, setError] = useState('')

  const handleFile = async (e) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setError('')
    const isExcel = /\.xlsx$/i.test(file.name)
    if (file.size > (isExcel ? 5 : 2) * 1024 * 1024) {
      setError(`File is larger than ${isExcel ? 5 : 2}MB`)
      return
    }
    let rows
    if (isExcel) {
      // .xlsx is a zipped XML format; the backend parses it (first worksheet)
      const formData = new FormData()
      formData.append('file', file)
      try {
        const res = await api.post('/import/xlsx', formData, { headers: { 'Content-Type': 'multipart/form-data' } })
        rows = res.data.rows
      } catch (err) {
        setError(errorMessage(err, 'Could not read the spreadsheet'))
        return
      }
    } else {
      rows = parseCsv(await file.text())
    }
    if (rows.length < 2) {
      setError('The file needs a header row and at least one data row')
      return
    }
    const [headers, ...data] = rows
    setTable({ fileName: file.name, headers: headers.map((h) => h.trim()), rows: data })
    setMapping(guessMapping(headers))
  }

  const mapped = table && mapping.email != null
    ? table.rows
        .map((row) => {
          const out = {}
          for (const f of CSV_FIELDS) out[f.key] = mapping[f.key] != null ? (row[mapping[f.key]] || '').trim() : ''
          const m = out.email.match(EMAIL_RE)
          return m ? { ...out, email: m[0] } : null
        })
        .filter(Boolean)
    : []

  if (!table) {
    return (
      <div>
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv,.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={handleFile}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="text-[11px] font-mono font-medium text-indigo-700 hover:bg-indigo-50 px-2 py-1 border border-indigo-200 uppercase tracking-wider"
        >
          Import CSV / Excel
        </button>
        {error && <p className="text-xs text-red-600 mt-1">{error}</p>}
      </div>
    )
  }

  return (
    <div className="border border-indigo-200 bg-indigo-50/40 p-4 mt-3">
      <div className="flex justify-between items-center mb-3">
        <span className="text-xs font-mono text-indigo-900">
          {table.fileName} · {table.rows.length} row{table.rows.length !== 1 ? 's' : ''}
        </span>
        <button type="button" onClick={() => setTable(null)} className="text-xs text-zinc-500 hover:underline">cancel</button>
      </div>

      <div className="grid sm:grid-cols-2 gap-2 mb-3">
        {CSV_FIELDS.map((f) => (
          <label key={f.key} className="flex items-center justify-between gap-2 text-xs">
            <span className="text-zinc-700">{f.label}{f.required ? ' *' : ''}</span>
            <select
              value={mapping[f.key] ?? ''}
              onChange={(e) => setMapping((m) => ({ ...m, [f.key]: e.target.value === '' ? null : Number(e.target.value) }))}
              className="border border-zinc-300 bg-white px-2 py-1 text-xs font-mono max-w-[12rem]"
            >
              <option value="">— none —</option>
              {table.headers.map((h, i) => <option key={i} value={i}>{h || `Column ${i + 1}`}</option>)}
            </select>
          </label>
        ))}
      </div>

      {mapped.length > 0 && (
        <div className="text-[11px] font-mono text-zinc-600 bg-white border border-zinc-200 p-2 mb-3 overflow-x-auto">
          <div className="font-bold mb-1">Preview</div>
          {mapped.slice(0, 3).map((r, i) => (
            <div key={i} className="whitespace-nowrap">
              {r.email}{r.name ? ` · ${r.name}` : ''}{r.company ? ` · ${r.company}` : ''}{r.role ? ` · ${r.role}` : ''}
              {r.notes ? ` · “${r.notes.slice(0, 40)}${r.notes.length > 40 ? '…' : ''}”` : ''}
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        disabled={mapped.length === 0}
        onClick={() => { onImport(mapped); setTable(null) }}
        className="px-4 py-1.5 bg-indigo-700 text-white text-[10px] font-bold uppercase tracking-widest hover:bg-indigo-800 disabled:opacity-50"
      >
        {mapping.email == null ? 'Choose the email column' : `Use ${mapped.length} recipient${mapped.length !== 1 ? 's' : ''}`}
      </button>
    </div>
  )
}
