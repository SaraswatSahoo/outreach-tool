import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api'
import DuplicateWarning from '../components/DuplicateWarning'
import GenerationProgress from '../components/GenerationProgress'
import CsvImport from '../components/CsvImport'
import usePoller from '../hooks/usePoller'
import { useBlockedDomains } from '../context/BlockedDomainsContext'
import { errorMessage, useUI } from '../context/UIContext'

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/

// Every distinct address in the pasted text (case-insensitive), in order.
// The backend parser additionally extracts names; this is only for previews.
function extractEmails(text) {
  const seen = new Set()
  const result = []
  for (const match of text.matchAll(new RegExp(EMAIL_RE, 'g'))) {
    const lower = match[0].toLowerCase()
    if (!seen.has(lower)) {
      seen.add(lower)
      result.push(match[0])
    }
  }
  return result
}

// Drop entries whose address exactly matches (case-insensitive) one of `emails`.
// Substring matching would also remove e.g. "ba@x.com" when skipping "a@x.com".
function removeEmails(text, emails) {
  const drop = new Set(emails.map(e => e.toLowerCase()))
  return text
    .split(/[\n,]+/)
    .filter(entry => {
      const m = entry.match(EMAIL_RE)
      return !(m && drop.has(m[0].toLowerCase()))
    })
    .join('\n')
}

export default function NewCampaign() {
  const navigate = useNavigate()
  const poller = usePoller()
  const { toast } = useUI()
  const { isBlocked } = useBlockedDomains()
  const [form, setForm] = useState({
    name: '',
    email_list: '',
    goal: '',
    additional_context: '',
  })
  const [loading, setLoading] = useState(false)
  const [generationProgress, setGenerationProgress] = useState(null)
  const [campaignId, setCampaignId] = useState(null)
  const [error, setError] = useState('')

  const [checked, setChecked] = useState(null) // { total, duplicates } from the last duplicate check
  const [pendingDuplicates, setPendingDuplicates] = useState([])
  const [skippedCount, setSkippedCount] = useState(0)
  const [csvRows, setCsvRows] = useState(null) // structured recipients from a CSV import

  const handleChange = (e) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
  }

  const currentEmails = csvRows ? csvRows.map((r) => r.email) : extractEmails(form.email_list)
  const blockedInPaste = currentEmails.filter((e) => isBlocked(e))

  const checkDuplicates = async (emails) => {
    setSkippedCount(0)
    if (emails.length === 0) {
      setChecked(null)
      setPendingDuplicates([])
      return
    }
    try {
      const res = await api.post('/campaign/check-duplicates', { emails })
      const dups = res.data.duplicates || []
      setChecked({ total: emails.length, duplicates: dups.length })
      setPendingDuplicates(dups)
    } catch (err) {
      toast.error(errorMessage(err, 'Duplicate check failed'))
    }
  }

  const skip = (emails) => {
    const drop = new Set(emails.map((e) => e.toLowerCase()))
    if (csvRows) setCsvRows((rows) => rows.filter((r) => !drop.has(r.email.toLowerCase())))
    else setForm((f) => ({ ...f, email_list: removeEmails(f.email_list, emails) }))
    setPendingDuplicates((prev) => prev.filter((d) => !drop.has(d.email.toLowerCase())))
    setSkippedCount((n) => n + emails.length)
  }

  const handleDecision = (email, action) => {
    if (action === 'skip') skip([email])
    else setPendingDuplicates((prev) => prev.filter((d) => d.email !== email))
  }

  const parseSummary = checked
    ? `✓ ${checked.total} valid emails parsed · ${checked.duplicates} already contacted ` +
      `(${skippedCount} skipped, ${checked.duplicates - skippedCount} included) · ${checked.total - checked.duplicates} new`
    : ''

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    let createdId = null
    try {
      const payload = csvRows ? { ...form, email_list: '', recipients: csvRows } : form
      const createRes = await api.post('/campaign/new', payload)
      const { campaign_id, recipients_count, skipped_blocked } = createRes.data
      createdId = campaign_id
      setCampaignId(campaign_id)
      if (skipped_blocked?.length) {
        toast.info(`Skipped ${skipped_blocked.length} email(s) from blocked domains`)
      }

      setGenerationProgress({ total: recipients_count, completed: 0, failed: 0, status: 'generating', errors: [] })
      await api.post(`/campaign/${campaign_id}/generate`)

      poller.start(async () => {
        const { data } = await api.get(`/campaign/${campaign_id}/generate-progress`)
        setGenerationProgress(data)
        if (data.status === 'generating') return false
        if (data.status === 'complete') navigate(`/campaign/${campaign_id}/preview`)
        return true
      }, 1500, () => setGenerationProgress((prev) => ({ ...prev, status: 'error' })))
    } catch (err) {
      // Don't leave an empty campaign behind if generation couldn't even start
      if (createdId) await api.delete(`/campaign/${createdId}`).catch(() => {})
      setGenerationProgress(null)
      setCampaignId(null)
      setError(errorMessage(err, 'Failed to start generation'))
      setLoading(false)
    }
  }

  if (generationProgress) {
    return (
      <div id="generating-screen" className="max-w-2xl mx-auto py-16 px-4 sm:px-8 border border-zinc-200 bg-white mt-10">
        <GenerationProgress progress={generationProgress} />
        {generationProgress.status !== 'generating' && generationProgress.status !== 'complete' && campaignId ? (
          <div className="mt-8 text-center">
            <p className="text-xs font-mono text-zinc-500 mb-4">
              Drafts that were generated are saved. You can regenerate the missing ones from the preview.
            </p>
            <button
              onClick={() => navigate(`/campaign/${campaignId}/preview`)}
              className="px-6 py-3 bg-indigo-700 text-white font-display font-bold uppercase tracking-widest text-xs hover:bg-indigo-800 transition-colors"
            >
              Continue to Preview
            </button>
          </div>
        ) : null}
      </div>
    )
  }

  return (
    <div id="new-campaign-page" className="max-w-2xl mx-auto pb-12">
      <div className="mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">New Campaign</h1>
      </div>

      {error ? (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 text-red-700 rounded-none text-sm font-mono">
          {error}
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* Campaign Name */}
        <div>
          <label htmlFor="campaign-name" className="block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2">
            Campaign Name *
          </label>
          <input
            id="campaign-name"
            type="text"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            placeholder="e.g., Summer Internship - ML Companies June 2026"
            className="w-full border border-zinc-300 rounded-none px-4 py-2.5 text-sm focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors"
          />
        </div>

        {/* Email List */}
        <div>
          <label htmlFor="email-list" className="block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2 flex items-center justify-between">
            <span>Email List *</span>
            <span className="text-zinc-500 font-mono normal-case tracking-normal text-[10px] bg-zinc-100 px-2 py-0.5 border border-zinc-200">paste any format</span>
          </label>
          {csvRows ? (
            <div className="border border-zinc-300 bg-white">
              <div className="flex justify-between items-center px-4 py-2 border-b border-zinc-200 bg-zinc-50">
                <span className="text-xs font-mono text-zinc-700">
                  {csvRows.length} recipient{csvRows.length !== 1 ? 's' : ''} imported
                </span>
                <button
                  type="button"
                  onClick={() => { setCsvRows(null); setChecked(null); setPendingDuplicates([]) }}
                  className="text-xs text-red-600 hover:underline"
                >
                  Clear import
                </button>
              </div>
              <div className="max-h-56 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="text-zinc-500 text-left">
                    <tr>{['Email', 'Name', 'Company', 'Role', 'Notes'].map((h) => <th key={h} className="px-3 py-1.5 font-medium">{h}</th>)}</tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100 font-mono">
                    {csvRows.map((r) => (
                      <tr key={r.email}>
                        <td className="px-3 py-1.5">{r.email}</td>
                        <td className="px-3 py-1.5">{r.name}</td>
                        <td className="px-3 py-1.5">{r.company}</td>
                        <td className="px-3 py-1.5">{r.role}</td>
                        <td className="px-3 py-1.5 max-w-[12rem] truncate" title={r.notes}>{r.notes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <textarea
              id="email-list"
              name="email_list"
              value={form.email_list}
              onChange={handleChange}
              onBlur={(e) => checkDuplicates(extractEmails(e.target.value))}
              required
              rows={6}
              placeholder={"john@company.com\njane.doe@startup.io, mark@techcorp.com\nAlice <alice@acme.com>\nBob Smith - bob@smith.com"}
              className="w-full border border-zinc-300 rounded-none px-4 py-3 text-sm font-mono leading-relaxed focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors"
            />
          )}

          <div className="flex flex-wrap justify-between items-center gap-2 mt-2">
             <p className="text-[11px] font-mono text-zinc-500">
               {csvRows
                 ? 'Company, role and notes are used to personalise each email.'
                 : 'Supports: newline, comma, space-separated, Name <email>, Name - email — or import a CSV/Excel file with company/role/notes columns.'}
             </p>
             <button type="button" onClick={() => checkDuplicates(currentEmails)} className="text-[11px] font-mono font-medium text-indigo-700 hover:bg-indigo-50 px-2 py-1 rounded-none border border-indigo-200 transition-colors uppercase tracking-wider">
               Check Duplicates
             </button>
          </div>

          {!csvRows && (
            <div className="mt-2">
              <CsvImport onImport={(rows) => { setCsvRows(rows); checkDuplicates(rows.map((r) => r.email)) }} />
            </div>
          )}
          
          {parseSummary ? (
            <div className="mt-3 text-xs font-mono text-emerald-800 bg-zinc-50 px-4 py-2.5 rounded-none border border-zinc-200 border-l-2 border-l-emerald-500 flex items-center gap-2">
               {parseSummary}
            </div>
          ) : null}

          {blockedInPaste.length > 0 && (
            <div className="text-xs font-mono text-red-800 bg-red-50 border border-red-200 rounded-none p-4 mt-3">
              <span className="font-bold">⚠️ BLOCKED DOMAINS DETECTED</span>
              <p className="mt-1 mb-2">The following emails are from blocked domains and will be skipped:</p>
              <ul className="list-disc list-inside text-[11px]">
                {blockedInPaste.map(e => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          {pendingDuplicates.length > 0 && (
            <DuplicateWarning
              duplicates={pendingDuplicates}
              onSkipAll={() => skip(pendingDuplicates.map((d) => d.email))}
              onIncludeAll={() => setPendingDuplicates([])}
              onPerRecipientDecision={handleDecision}
            />
          )}
        </div>

        {/* Campaign Goal */}
        <div>
          <label htmlFor="campaign-goal" className="block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2">
            Campaign Goal / Description *
          </label>
          <textarea
            id="campaign-goal"
            name="goal"
            value={form.goal}
            onChange={handleChange}
            required
            rows={3}
            placeholder="e.g., Send emails asking for a summer internship in machine learning or chemical engineering roles"
            className="w-full border border-zinc-300 rounded-none px-4 py-3 text-sm leading-relaxed focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors"
          />
        </div>

        {/* Additional Context */}
        <div>
          <label htmlFor="additional-context" className="block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2 flex items-center justify-between">
            <span>Additional Context</span>
            <span className="text-zinc-400 font-mono normal-case tracking-normal text-[10px]">optional</span>
          </label>
          <textarea
            id="additional-context"
            name="additional_context"
            value={form.additional_context}
            onChange={handleChange}
            rows={2}
            placeholder="e.g., Mention that I'm available from May to July 2026"
            className="w-full border border-zinc-300 rounded-none px-4 py-3 text-sm leading-relaxed focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors"
          />
        </div>

        <div className="pt-4 border-t border-zinc-200">
          <button
            id="create-campaign-btn"
            type="submit"
            disabled={loading}
            className="w-full py-3.5 bg-indigo-700 text-white font-display font-bold uppercase tracking-widest text-sm rounded-none hover:bg-indigo-800 disabled:opacity-50 disabled:bg-zinc-400 transition-colors"
          >
            {loading ? 'Starting...' : 'Create Campaign & Generate'}
          </button>
        </div>
      </form>
    </div>
  )
}
