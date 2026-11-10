import { useEffect, useState } from 'react'
import api from '../api'

const EDITS_KEY = 'outreach.templateEdits' // your edits to each template, remembered in this browser

function loadEdits() {
  try {
    return JSON.parse(localStorage.getItem(EDITS_KEY)) || {}
  } catch {
    return {}
  }
}

function saveEdits(edits) {
  try {
    localStorage.setItem(EDITS_KEY, JSON.stringify(edits))
  } catch {
    // private window or storage blocked: edits just aren't remembered
  }
}

const domainOf = (email) => email.split('@').pop().toLowerCase()

/**
 * Pick a fixed email template, tweak it, and check how it reads for your recipients.
 * `value` is {id, subject, body, goal}; `companies` maps domain -> name typed by the user.
 */
export default function TemplatePicker({ value, onChange, recipients, companies, onCompaniesChange }) {
  const [templates, setTemplates] = useState([])
  const [placeholders, setPlaceholders] = useState({})
  const [guesses, setGuesses] = useState({})
  const [previewIndex, setPreviewIndex] = useState(0)
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    api.get('/templates')
      .then(({ data }) => {
        setTemplates(data.templates)
        setPlaceholders(data.placeholders)
        if (!value.id && data.templates.length) choose(data.templates[0])
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const choose = (template) => {
    const edited = loadEdits()[template.id]
    onChange({ id: template.id, goal: template.goal, subject: edited?.subject ?? template.subject, body: edited?.body ?? template.body })
  }

  const edit = (field, text) => {
    const next = { ...value, [field]: text }
    onChange(next)
    saveEdits({ ...loadEdits(), [value.id]: { subject: next.subject, body: next.body } })
  }

  const original = templates.find((t) => t.id === value.id)
  const isEdited = original && (original.subject !== value.subject || original.body !== value.body)
  const resetTemplate = () => {
    const edits = loadEdits()
    delete edits[value.id]
    saveEdits(edits)
    choose(original)
  }

  // Domains whose company name comes from the domain (CSV rows with a company keep theirs)
  const domains = [...new Set(recipients.filter((r) => !r.company).map((r) => domainOf(r.email)))]
  const domainKey = domains.join(',')
  useEffect(() => {
    const missing = domains.filter((d) => !(d in guesses))
    if (!missing.length) return
    const timer = setTimeout(() => {
      api.post('/templates/companies', { emails: missing.map((d) => `x@${d}`) })
        .then(({ data }) => setGuesses((g) => ({ ...g, ...data.companies })))
        .catch(() => {})
    }, 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domainKey])

  const index = Math.min(previewIndex, Math.max(recipients.length - 1, 0))
  const previewRecipient = recipients[index]
  const previewCompany = previewRecipient && !previewRecipient.company ? companies[domainOf(previewRecipient.email)] : ''
  // A string, so the preview only refreshes when the recipient's details actually change
  const previewKey = JSON.stringify(previewRecipient ? { ...previewRecipient, company: previewRecipient.company || previewCompany || '' } : {})
  useEffect(() => {
    if (!value.subject && !value.body) return
    const timer = setTimeout(() => {
      api.post('/templates/preview', { subject: value.subject, body: value.body, recipient: JSON.parse(previewKey) })
        .then(({ data }) => setPreview(data))
        .catch(() => setPreview(null))
    }, 300)
    return () => clearTimeout(timer)
  }, [value.subject, value.body, previewKey])

  const labelClass = 'block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2'
  const fieldClass = 'w-full border border-zinc-300 rounded-none px-4 py-3 text-sm leading-relaxed focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors'

  return (
    <div className="space-y-6">
      {/* Template choice */}
      <div>
        <span className={labelClass}>Email Template</span>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
          {templates.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => choose(t)}
              className={`text-left px-4 py-3 border transition-colors ${
                value.id === t.id ? 'border-indigo-700 bg-indigo-50 ring-1 ring-indigo-700' : 'border-zinc-300 bg-white hover:border-zinc-500'
              }`}
            >
              <div className="text-sm font-semibold text-zinc-900">{t.label}</div>
              <div className="text-[11px] text-zinc-500 mt-0.5">{t.hint}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Editor + preview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-3">
          <div>
            <label htmlFor="template-subject" className={`${labelClass} flex items-center justify-between`}>
              <span>Subject</span>
              {isEdited && (
                <button type="button" onClick={resetTemplate} className="text-[10px] font-mono normal-case tracking-normal text-indigo-700 hover:underline">
                  reset to original
                </button>
              )}
            </label>
            <input
              id="template-subject"
              value={value.subject}
              onChange={(e) => edit('subject', e.target.value)}
              className="w-full border border-zinc-300 rounded-none px-4 py-2.5 text-sm font-mono focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none bg-zinc-50 focus:bg-white"
            />
          </div>
          <div>
            <label htmlFor="template-body" className={labelClass}>Body</label>
            <textarea
              id="template-body"
              value={value.body}
              onChange={(e) => edit('body', e.target.value)}
              rows={14}
              className={`${fieldClass} font-mono text-xs`}
            />
          </div>
          <details className="text-[11px] font-mono text-zinc-500">
            <summary className="cursor-pointer select-none">Placeholders you can use</summary>
            <ul className="mt-2 space-y-1">
              {Object.entries(placeholders).map(([key, meaning]) => (
                <li key={key}><code className="text-zinc-900 bg-zinc-100 px-1">{`{${key}}`}</code> {meaning}</li>
              ))}
            </ul>
            <p className="mt-2">Your details come from the Profile page. Your resume is attached to every email.</p>
          </details>
        </div>

        <div>
          <div className={`${labelClass} flex items-center justify-between`}>
            <span>Preview</span>
            {recipients.length > 1 && (
              <span className="flex items-center gap-2 font-mono normal-case tracking-normal text-[10px] text-zinc-500">
                <button type="button" onClick={() => setPreviewIndex(Math.max(index - 1, 0))} disabled={index === 0} className="px-1.5 border border-zinc-300 disabled:opacity-40">‹</button>
                {index + 1} / {recipients.length}
                <button type="button" onClick={() => setPreviewIndex(Math.min(index + 1, recipients.length - 1))} disabled={index >= recipients.length - 1} className="px-1.5 border border-zinc-300 disabled:opacity-40">›</button>
              </span>
            )}
          </div>
          <div className="border border-zinc-300 bg-white">
            {preview ? (
              <>
                <div className="px-4 py-2 border-b border-zinc-200 text-xs font-mono text-zinc-500">
                  To: {preview.email}
                </div>
                <div className="px-4 py-2 border-b border-zinc-200 text-sm font-semibold text-zinc-900">{preview.subject}</div>
                <pre className="px-4 py-3 text-sm text-zinc-800 whitespace-pre-wrap font-sans leading-relaxed">{preview.body}</pre>
              </>
            ) : (
              <p className="px-4 py-6 text-xs font-mono text-zinc-400">The filled-in email shows here.</p>
            )}
          </div>
          {preview?.unknown?.length > 0 && (
            <p className="mt-2 text-xs text-red-600 font-mono">
              Unknown placeholder{preview.unknown.length > 1 ? 's' : ''}: {preview.unknown.map((u) => `{${u}}`).join(', ')}
            </p>
          )}
          {!recipients.length && (
            <p className="mt-2 text-[11px] font-mono text-zinc-500">Add emails above to preview it for a real recipient.</p>
          )}
        </div>
      </div>

      {/* Company names */}
      {domains.length > 0 && (
        <div>
          <span className={`${labelClass} flex items-center justify-between`}>
            <span>Company Names</span>
            <span className="text-zinc-400 font-mono normal-case tracking-normal text-[10px]">guessed from the email domain — fix any that look off</span>
          </span>
          <div className="border border-zinc-300 bg-white max-h-64 overflow-auto divide-y divide-zinc-100">
            {domains.map((domain) => (
              <div key={domain} className="flex items-center gap-3 px-3 py-1.5">
                <span className="w-1/2 truncate text-xs font-mono text-zinc-500" title={domain}>@{domain}</span>
                <input
                  value={companies[domain] ?? ''}
                  onChange={(e) => onCompaniesChange({ ...companies, [domain]: e.target.value })}
                  placeholder={guesses[domain] || 'personal email: uses "your company"'}
                  aria-label={`Company name for ${domain}`}
                  className="flex-1 border border-zinc-200 px-2 py-1 text-sm focus:ring-1 focus:ring-zinc-900 outline-none placeholder-zinc-500"
                />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
