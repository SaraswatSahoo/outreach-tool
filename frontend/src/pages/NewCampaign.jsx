import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../api'
import BriefBuilder from '../components/BriefBuilder'
import CsvImport from '../components/CsvImport'
import DuplicateWarning from '../components/DuplicateWarning'
import { CheckSummary, RemovedColumn } from '../components/EmailCheckPanel'
import GenerationProgress from '../components/GenerationProgress'
import SampleEmailPreview from '../components/SampleEmailPreview'
import usePoller from '../hooks/usePoller'
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

const appendLine = (text, line) => (text.trim() ? `${text.replace(/\s+$/, '')}\n${line}` : line)
const listKey = (emails) => emails.map((e) => e.toLowerCase()).sort().join(',')

export default function NewCampaign() {
  const navigate = useNavigate()
  const poller = usePoller()
  const checkPoller = usePoller()
  const { toast } = useUI()
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

  const [pendingDuplicates, setPendingDuplicates] = useState([])
  const [duplicateCount, setDuplicateCount] = useState(0)
  const [csvRows, setCsvRows] = useState(null) // structured recipients from a CSV/Excel import

  // Email checking
  const [checking, setChecking] = useState(false)
  const [checkProgress, setCheckProgress] = useState(null)
  const [checkStats, setCheckStats] = useState(null)
  const [removed, setRemoved] = useState([]) // {email, verdict, reason, suggestion, row?}
  const [risky, setRisky] = useState([])
  const [provider, setProvider] = useState(null)
  const lastChecked = useRef('')
  const restored = useRef(new Set()) // addresses the user put back: never auto-remove them again

  useEffect(() => {
    api.get('/settings')
      .then(({ data }) => {
        if (data.verify_provider && data.has_verify_key && data.verify_with_service) {
          setProvider(data.verify_providers?.find((p) => p.name === data.verify_provider)?.label || null)
        }
      })
      .catch(() => {})
  }, [])

  const handleChange = (e) => {
    setForm((prev) => ({ ...prev, [e.target.name]: e.target.value }))
  }

  const currentEmails = csvRows ? csvRows.map((r) => r.email) : extractEmails(form.email_list)

  const checkDuplicates = async (emails) => {
    if (emails.length === 0) {
      setPendingDuplicates([])
      setDuplicateCount(0)
      return
    }
    try {
      const res = await api.post('/campaign/check-duplicates', { emails })
      const dups = res.data.duplicates || []
      setDuplicateCount(dups.length)
      setPendingDuplicates(dups)
    } catch (err) {
      toast.error(errorMessage(err, 'Duplicate check failed'))
    }
  }

  // Take invalid addresses out of the list (keeping them in the Removed column)
  const applyResults = (results, rows) => {
    const isRestored = (email) => restored.current.has(email.toLowerCase())
    const bad = results.filter((r) => r.verdict === 'invalid' && !isRestored(r.email))
    const badSet = new Set(bad.map((r) => r.email.toLowerCase()))
    const keptResults = results.filter((r) => !badSet.has(r.email.toLowerCase()))

    if (rows) {
      const rowFor = new Map(rows.map((row) => [row.email.toLowerCase(), row]))
      bad.forEach((entry) => { entry.row = rowFor.get(entry.email.toLowerCase()) })
      setCsvRows(rows.filter((row) => !badSet.has(row.email.toLowerCase())))
    } else if (bad.length) {
      setForm((f) => ({ ...f, email_list: removeEmails(f.email_list, bad.map((r) => r.email)) }))
    }
    setRemoved((prev) => {
      const fresh = bad.filter((entry) => !prev.some((p) => p.email.toLowerCase() === entry.email.toLowerCase()))
      return [...fresh, ...prev]
    })
    setRisky(keptResults.filter((r) => r.verdict === 'risky' || isRestored(r.email)).map((r) =>
      isRestored(r.email) && r.verdict === 'invalid' ? { ...r, verdict: 'risky', reason: `kept by you — ${r.reason}` } : r,
    ))
    const kept = keptResults.map((r) => r.email)
    lastChecked.current = listKey(kept) // so the edited list isn't re-checked on the next blur
    setCheckStats({
      checked: results.length,
      good: keptResults.filter((r) => r.verdict === 'valid').length,
      risky: keptResults.filter((r) => r.verdict !== 'valid').length,
      removed: bad.length,
    })
    checkDuplicates(kept)
  }

  const runCheck = async (emails, rows = null, { force = false } = {}) => {
    if (emails.length === 0) {
      setCheckStats(null)
      setRisky([])
      setPendingDuplicates([])
      setDuplicateCount(0)
      lastChecked.current = ''
      return
    }
    const key = listKey(emails)
    if (!force && key === lastChecked.current) return
    lastChecked.current = key
    setChecking(true)
    setCheckProgress({ done: 0, total: emails.length })
    try {
      const { data } = await api.post('/emails/verify', { emails })
      checkPoller.start(async () => {
        const { data: job } = await api.get(`/emails/verify/${data.job_id}`)
        setCheckProgress({ done: job.done, total: job.total })
        if (job.status === 'checking') return false
        setChecking(false)
        if (job.status === 'complete') applyResults(job.results, rows)
        else toast.error(job.error || 'Checking the emails was interrupted — try again')
        return true
      }, 800, () => {
        setChecking(false)
        lastChecked.current = ''
        toast.error('Lost connection while checking emails')
      })
    } catch (err) {
      setChecking(false)
      lastChecked.current = ''
      toast.error(errorMessage(err, 'Couldn\'t check the emails'))
    }
  }

  const restore = (entry) => {
    restored.current.add(entry.email.toLowerCase())
    setRemoved((prev) => prev.filter((r) => r.email !== entry.email))
    if (csvRows) {
      setCsvRows((rows) => [...rows, entry.row || { email: entry.email }])
    } else {
      setForm((f) => ({ ...f, email_list: appendLine(f.email_list, entry.email) }))
    }
    setRisky((prev) => [...prev, { ...entry, verdict: 'risky', reason: `kept by you — ${entry.reason}` }])
    lastChecked.current = listKey([...currentEmails, entry.email])
  }

  const applySuggestion = (entry) => {
    const fixed = entry.suggestion
    setRemoved((prev) => prev.filter((r) => r.email !== entry.email))
    setRisky((prev) => prev.filter((r) => r.email !== entry.email))
    const others = currentEmails.filter((e) => e.toLowerCase() !== entry.email.toLowerCase())
    if (csvRows) {
      const base = csvRows.filter((r) => r.email.toLowerCase() !== entry.email.toLowerCase())
      const row = { ...(entry.row || csvRows.find((r) => r.email === entry.email) || {}), email: fixed }
      setCsvRows([...base, row])
      runCheck([...others, fixed], [...base, row], { force: true })
    } else {
      const text = appendLine(removeEmails(form.email_list, [entry.email]), fixed)
      setForm((f) => ({ ...f, email_list: text }))
      runCheck(extractEmails(text), null, { force: true })
    }
  }

  const skip = (emails) => {
    const drop = new Set(emails.map((e) => e.toLowerCase()))
    if (csvRows) setCsvRows((rows) => rows.filter((r) => !drop.has(r.email.toLowerCase())))
    else setForm((f) => ({ ...f, email_list: removeEmails(f.email_list, emails) }))
    setPendingDuplicates((prev) => prev.filter((d) => !drop.has(d.email.toLowerCase())))
    lastChecked.current = listKey(currentEmails.filter((e) => !drop.has(e.toLowerCase())))
  }

  const handleDecision = (email, action) => {
    if (action === 'skip') skip([email])
    else setPendingDuplicates((prev) => prev.filter((d) => d.email !== email))
  }

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

  const showRemovedColumn = checkStats || removed.length > 0
  const firstRecipient = csvRows?.[0] || (currentEmails[0] ? { email: currentEmails[0] } : null)
  const labelClass = 'block text-xs font-display font-bold text-zinc-950 uppercase tracking-widest mb-2'
  const fieldClass = 'w-full border border-zinc-300 rounded-none px-4 py-3 text-sm leading-relaxed focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors'

  return (
    <div id="new-campaign-page" className="max-w-4xl mx-auto pb-12">
      <div className="mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">New Campaign</h1>
      </div>

      {error ? (
        <div className="mb-6 p-4 bg-red-50 border border-red-200 text-red-700 rounded-none text-sm font-mono">
          {error}
        </div>
      ) : null}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* Campaign Name */}
        <div>
          <label htmlFor="campaign-name" className={labelClass}>Campaign Name *</label>
          <input
            id="campaign-name"
            type="text"
            name="name"
            value={form.name}
            onChange={handleChange}
            required
            placeholder="e.g., Summer Internship - ML Startups"
            className="w-full border border-zinc-300 rounded-none px-4 py-2.5 text-sm focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none placeholder-zinc-400 bg-zinc-50 focus:bg-white transition-colors"
          />
        </div>

        {/* Email List */}
        <div>
          <label htmlFor="email-list" className={`${labelClass} flex items-center justify-between`}>
            <span>Email List *</span>
            <span className="text-zinc-500 font-mono normal-case tracking-normal text-[10px] bg-zinc-100 px-2 py-0.5 border border-zinc-200">
              checked automatically
            </span>
          </label>

          <div className={showRemovedColumn ? 'grid grid-cols-1 lg:grid-cols-3 gap-4' : ''}>
            <div className={showRemovedColumn ? 'lg:col-span-2' : ''}>
              {csvRows ? (
                <div className="border border-zinc-300 bg-white">
                  <div className="flex justify-between items-center px-4 py-2 border-b border-zinc-200 bg-zinc-50">
                    <span className="text-xs font-mono text-zinc-700">
                      {csvRows.length} recipient{csvRows.length !== 1 ? 's' : ''} imported
                    </span>
                    <button
                      type="button"
                      onClick={() => { setCsvRows(null); runCheck([]); setRemoved([]) }}
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
                  onBlur={(e) => runCheck(extractEmails(e.target.value))}
                  required
                  rows={8}
                  placeholder={"john@company.com\njane.doe@startup.io, mark@techcorp.com\nAlice <alice@acme.com>\nBob Smith - bob@smith.com"}
                  className={`${fieldClass} font-mono`}
                />
              )}
            </div>
            {showRemovedColumn && (
              <RemovedColumn removed={removed} onRestore={restore} onUseSuggestion={applySuggestion} />
            )}
          </div>

          <div className="flex flex-wrap justify-between items-center gap-2 mt-2">
            <p className="text-[11px] font-mono text-zinc-500">
              {csvRows
                ? 'Company, role and notes are used to personalise each email.'
                : 'Paste in any format (one per line, commas, Name <email>) — or import a CSV/Excel file below.'}
            </p>
            <button
              type="button"
              onClick={() => runCheck(currentEmails, csvRows, { force: true })}
              disabled={checking || currentEmails.length === 0}
              className="text-[11px] font-mono font-medium text-indigo-700 hover:bg-indigo-50 px-2 py-1 rounded-none border border-indigo-200 transition-colors uppercase tracking-wider disabled:opacity-50"
            >
              {checking ? 'Checking…' : 'Check emails'}
            </button>
          </div>

          {!csvRows && (
            <div className="mt-2">
              <CsvImport onImport={(rows) => { setCsvRows(rows); runCheck(rows.map((r) => r.email), rows, { force: true }) }} />
            </div>
          )}

          <CheckSummary
            checking={checking}
            progress={checkProgress}
            stats={checkStats && { ...checkStats, duplicates: duplicateCount }}
            risky={risky}
            onUseSuggestion={applySuggestion}
            provider={provider}
          />

          {pendingDuplicates.length > 0 && (
            <DuplicateWarning
              duplicates={pendingDuplicates}
              onSkipAll={() => skip(pendingDuplicates.map((d) => d.email))}
              onIncludeAll={() => setPendingDuplicates([])}
              onPerRecipientDecision={handleDecision}
            />
          )}
        </div>

        {/* Brief */}
        <div className="space-y-4">
          <BriefBuilder
            hasGoal={!!(form.goal.trim() || form.additional_context.trim())}
            onApply={({ goal, additional_context }) => setForm((f) => ({ ...f, goal, additional_context }))}
          />

          <div>
            <label htmlFor="campaign-goal" className={labelClass}>Campaign Goal / Description *</label>
            <textarea
              id="campaign-goal"
              name="goal"
              value={form.goal}
              onChange={handleChange}
              required
              rows={3}
              placeholder="What you're asking for, from whom, and when — or use “Help me write this” above."
              className={fieldClass}
            />
          </div>

          <div>
            <label htmlFor="additional-context" className={`${labelClass} flex items-center justify-between`}>
              <span>Additional Context</span>
              <span className="text-zinc-400 font-mono normal-case tracking-normal text-[10px]">optional</span>
            </label>
            <textarea
              id="additional-context"
              name="additional_context"
              value={form.additional_context}
              onChange={handleChange}
              rows={3}
              placeholder="Facts the emails can use: availability, a project to highlight, links, tone. The AI won't invent these."
              className={fieldClass}
            />
          </div>

          <SampleEmailPreview goal={form.goal} additionalContext={form.additional_context} recipient={firstRecipient} />
        </div>

        <div className="pt-4 border-t border-zinc-200">
          <button
            id="create-campaign-btn"
            type="submit"
            disabled={loading || checking}
            className="w-full py-3.5 bg-indigo-700 text-white font-display font-bold uppercase tracking-widest text-sm rounded-none hover:bg-indigo-800 disabled:opacity-50 disabled:bg-zinc-400 transition-colors"
          >
            {loading ? 'Starting...' : checking ? 'Checking emails…' : 'Create Campaign & Generate'}
          </button>
        </div>
      </form>
    </div>
  )
}
