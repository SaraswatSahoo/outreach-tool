import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api'
import GenerationProgress from '../components/GenerationProgress'
import usePoller from '../hooks/usePoller'
import { useBlockedDomains } from '../context/BlockedDomainsContext'
import { formatDateTime } from '../utils/dates'
import { errorMessage, useUI } from '../context/UIContext'

export default function Preview({ isFollowUp = false }) {
  const { campaignId } = useParams()
  const navigate = useNavigate()
  const poller = usePoller()
  const { toast, confirm } = useUI()
  const { isBlocked } = useBlockedDomains()
  const [campaign, setCampaign] = useState(null)
  const [items, setItems] = useState([])
  const [dirty, setDirty] = useState(new Set()) // ids with unsaved edits
  const [loading, setLoading] = useState(true)
  const [regenerating, setRegenerating] = useState({})
  const [generation, setGeneration] = useState(null)
  const [error, setError] = useState('')
  const [sentCount, setSentCount] = useState(0)

  const load = useCallback(() => {
    const url = isFollowUp
      ? `/campaign/${campaignId}/followup/preview`
      : `/campaign/${campaignId}/preview`

    return api.get(url)
      .then((res) => {
        if (isFollowUp) {
          setItems(res.data.followups || [])
        } else {
          setCampaign(res.data.campaign)
          const all = res.data.recipients || []
          // Only unsent drafts belong in the preview; already-sent/failed ones are history
          setItems(all.filter((r) => r.status === 'draft'))
          setSentCount(all.filter((r) => r.status === 'sent').length)
        }
      })
      .catch((err) => setError(errorMessage(err, 'Failed to load preview')))
      .finally(() => setLoading(false))
  }, [campaignId, isFollowUp])

  useEffect(() => { load() }, [load])

  const emailKey = isFollowUp ? 'recipient_email' : 'email'
  const nameKey = isFollowUp ? 'recipient_name' : 'name'

  const handleEdit = (index, field, value) => {
    setItems((prev) => {
      const updated = [...prev]
      updated[index] = { ...updated[index], [field]: value }
      return updated
    })
    setDirty((prev) => new Set(prev).add(items[index].id))
  }

  const saveEdit = async (item) => {
    if (!dirty.has(item.id)) return true
    try {
      const url = isFollowUp ? `/followup/${item.id}` : `/campaign/${campaignId}/recipient/${item.id}`
      await api.put(url, { subject: item.subject, email_body: item.email_body })
      setDirty((prev) => {
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
      return true
    } catch (err) {
      toast.error(`Couldn't save edit for ${item[emailKey]}: ${errorMessage(err)}`)
      return false
    }
  }

  const handleRegenerate = async (item, index) => {
    if (isFollowUp) return // Follow-ups don't support individual regeneration
    if (dirty.has(item.id)) {
      const ok = await confirm({
        title: 'Discard your edits?',
        message: 'Regenerating replaces the edited subject and body for this recipient.',
        confirmLabel: 'Regenerate',
      })
      if (!ok) return
    }
    setRegenerating((prev) => ({ ...prev, [item.id]: true }))

    try {
      const res = await api.post(`/campaign/${campaignId}/recipient/${item.id}/regenerate`)
      setItems((prev) => {
        const updated = [...prev]
        updated[index] = { ...updated[index], subject: res.data.subject, email_body: res.data.body }
        return updated
      })
      setDirty((prev) => {
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to regenerate'))
    } finally {
      setRegenerating((prev) => ({ ...prev, [item.id]: false }))
    }
  }

  // Generate every draft that is still missing an email (e.g. after rate limits)
  const handleGenerateMissing = async () => {
    try {
      const res = await api.post(`/campaign/${campaignId}/generate`)
      setGeneration({ total: res.data.total, completed: 0, failed: 0, status: 'generating', errors: [] })
      poller.start(async () => {
        const { data } = await api.get(`/campaign/${campaignId}/generate-progress`)
        setGeneration(data)
        if (data.status === 'generating') return false
        await load()
        return true
      }, 1500, () => setGeneration(null))
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to start generation'))
    }
  }

  const handleDeleteItem = async (item) => {
    const ok = await confirm({
      title: isFollowUp ? 'Remove follow-up?' : 'Remove recipient?',
      message: isFollowUp
        ? `The follow-up to ${item[emailKey]} will be deleted.`
        : `${item[emailKey]} will be removed from this campaign.`,
      confirmLabel: 'Remove',
      danger: true,
    })
    if (!ok) return

    try {
      await api.delete(isFollowUp ? `/followup/${item.id}` : `/recipient/${item.id}`)
      setItems((prev) => prev.filter((i) => i.id !== item.id))
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to delete item'))
    }
  }

  const blockedItems = items.filter(item => isBlocked(item[emailKey]))
  // Drafts whose generation failed have no subject; the backend skips them
  const emptyItems = items.filter(item => !item.subject && !isBlocked(item[emailKey]))
  const sendableCount = items.length - blockedItems.length - emptyItems.length

  const handleSendAll = async () => {
    const ok = await confirm({
      title: 'Confirm Send',
      message: `You are about to send ${sendableCount} email${sendableCount !== 1 ? 's' : ''}. This cannot be undone. Proceed?`,
      confirmLabel: 'Send',
    })
    if (!ok) return

    // Save any pending edits first; don't send stale content if a save failed
    const saved = await Promise.all(items.map((item) => saveEdit(item)))
    if (saved.includes(false)) return

    const path = isFollowUp ? `/campaign/${campaignId}/followup/send` : `/campaign/${campaignId}/send`
    try {
      await api.post(path)
      navigate(path)
    } catch (err) {
      if (err.response?.status === 409) navigate(path) // already sending: show its progress
      else setError(errorMessage(err, 'Failed to start sending'))
    }
  }

  const handleDiscard = async () => {
    if (isFollowUp || sentCount > 0) {
      // Never delete a campaign that already has sent history from the preview screen
      navigate(`/campaign/${campaignId}`)
      return
    }
    const ok = await confirm({
      title: 'Discard campaign?',
      message: `"${campaign?.name || 'This campaign'}" and all ${items.length} draft email(s) will be permanently deleted.`,
      confirmLabel: 'Discard',
      danger: true,
    })
    if (!ok) return
    try {
      await api.delete(`/campaign/${campaignId}`)
      navigate('/campaigns')
    } catch (err) {
      setError(errorMessage(err, 'Failed to delete campaign'))
    }
  }

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading preview...</div>
  }

  return (
    <div id="preview-page" className="max-w-4xl mx-auto pb-12">
      <div className="flex items-center justify-between mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">
          {isFollowUp ? 'Follow-up Preview' : 'Email Preview'}
        </h1>
        <div className="text-xs font-mono bg-zinc-100 border border-zinc-200 px-3 py-1 text-zinc-600">
          {items.length} EMAIL{items.length !== 1 ? 'S' : ''}
          {blockedItems.length > 0 && (
            <span className="ml-2 text-red-600">({blockedItems.length} BLOCKED)</span>
          )}
        </div>
      </div>

      {error ? (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-md text-sm">
          {error}
        </div>
      ) : null}

      {emptyItems.length > 0 && (
        <div className="text-xs font-mono text-amber-800 bg-amber-50 border border-amber-200 rounded-none px-4 py-3 mb-6">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <span>
              <span className="font-bold">⚠️ NOT GENERATED:</span> {emptyItems.length} draft{emptyItems.length !== 1 ? 's' : ''} failed to generate and will be skipped.
            </span>
            {!isFollowUp && !generation && (
              <button
                onClick={handleGenerateMissing}
                className="px-3 py-1.5 bg-amber-600 text-white uppercase tracking-wider hover:bg-amber-700 whitespace-nowrap"
              >
                Generate {emptyItems.length} missing
              </button>
            )}
          </div>
          {generation && (
            <div className="mt-3">
              <GenerationProgress progress={generation} />
            </div>
          )}
        </div>
      )}

      {blockedItems.length > 0 && (
        <div className="text-xs font-mono text-red-800 bg-red-50 border border-red-200 rounded-none px-4 py-3 mb-6">
          <span className="font-bold">⚠️ BLOCKED DOMAINS:</span> {blockedItems.length} recipient{blockedItems.length !== 1 ? 's' : ''} from blocked domains will be skipped.
          Only <strong>{sendableCount}</strong> email{sendableCount !== 1 ? 's' : ''} will actually be sent.
        </div>
      )}

      {/* Top controls */}
      <div className="flex flex-wrap gap-4 mb-8">
        <button
          id="send-all-top-btn"
          onClick={handleSendAll}
          disabled={items.length === 0 || sendableCount === 0}
          className="px-6 py-2.5 bg-indigo-700 text-white font-display font-bold text-xs rounded-none hover:bg-indigo-800 disabled:bg-zinc-300 disabled:text-zinc-500 disabled:cursor-not-allowed transition-colors uppercase tracking-widest"
        >
          {sendableCount > 0 ? `Send ${sendableCount} Email${sendableCount !== 1 ? 's' : ''}` : 'No Emails to Send'}
        </button>
        {!isFollowUp && sendableCount > 0 && (
          <ScheduleButton campaignId={campaignId} scheduledAt={campaign?.scheduled_at}
            onChange={(value) => setCampaign((prev) => ({ ...prev, scheduled_at: value }))} />
        )}
        <button
          id="discard-btn"
          onClick={handleDiscard}
          className="px-6 py-2.5 bg-zinc-50 border border-zinc-300 text-zinc-700 font-display font-bold text-xs rounded-none hover:bg-zinc-100 transition-colors uppercase tracking-widest"
        >
          {isFollowUp || sentCount > 0 ? 'Back to Campaign' : 'Discard Campaign'}
        </button>
      </div>

      {/* Email cards */}
      <div className="space-y-6">
        {items.map((item, index) => (
          <div
            key={item.id}
            className={`bg-white border rounded-none p-6 ${
              isBlocked(item[emailKey])
                ? 'border-red-300 bg-red-50/50'
                : 'border-zinc-300'
            }`}
          >
            {isBlocked(item[emailKey]) && (
              <div className="text-[10px] font-mono text-red-700 bg-red-100 border border-red-200 rounded-none px-3 py-1.5 mb-4 inline-flex items-center gap-1.5">
                <span className="font-bold">BLOCKED:</span> {item[emailKey].split('@')[1]}
              </div>
            )}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-5 pb-3 border-b border-zinc-100 gap-4">
              <div>
                <div className="font-display font-bold text-zinc-950 text-lg">
                  {item[nameKey] || 'No name'}
                </div>
                <div className="text-sm font-mono text-zinc-500 mt-1">
                  {item[emailKey]}
                </div>
                {(item.company || item.role) && (
                  <div className="text-xs text-zinc-500 mt-1">{[item.role, item.company].filter(Boolean).join(' · ')}</div>
                )}
                {isFollowUp && item.step > 1 && (
                  <span className="inline-block mt-2 mr-2 text-[10px] font-mono px-2 py-0.5 border border-indigo-200 bg-indigo-50 text-indigo-800 uppercase tracking-wider">
                    Follow-up #{item.step}
                  </span>
                )}
                {isFollowUp && item.source === 'sequence' && (
                  <span className="inline-block mt-2 mr-2 text-[10px] font-mono px-2 py-0.5 border border-zinc-200 bg-zinc-50 text-zinc-600 uppercase tracking-wider">
                    Auto-drafted
                  </span>
                )}
                {isFollowUp && item.reply_status && (
                  <span className="inline-block mt-2 text-[10px] font-mono px-2 py-0.5 border border-amber-200 bg-amber-50 text-amber-800 uppercase tracking-wider">
                    Context: {item.reply_status.replace(/_/g, ' ')}
                  </span>
                )}
              </div>
              <div className="flex gap-2">
                {!isFollowUp && (
                  <button
                    onClick={() => handleRegenerate(item, index)}
                    disabled={regenerating[item.id]}
                    className="text-[11px] font-mono px-3 py-1.5 border border-zinc-300 bg-zinc-50 rounded-none text-zinc-700 hover:bg-zinc-100 disabled:opacity-50 transition-colors uppercase tracking-wider"
                  >
                    {regenerating[item.id] ? 'Regenerating...' : 'Regenerate'}
                  </button>
                )}
                <button
                  onClick={() => handleDeleteItem(item)}
                  title="Remove from campaign"
                  className="text-[11px] font-mono px-3 py-1.5 border border-red-200 bg-white rounded-none text-red-600 hover:bg-red-50 transition-all uppercase tracking-wider"
                >
                  Remove
                </button>
              </div>
            </div>

            {/* Subject */}
            <div className="mb-4">
              <label className="text-[10px] font-display font-bold text-zinc-500 mb-1.5 block uppercase tracking-widest">Subject</label>
              <input
                type="text"
                value={item.subject || ''}
                onChange={(e) => handleEdit(index, 'subject', e.target.value)}
                onBlur={() => saveEdit(item)}
                className="w-full border border-zinc-300 rounded-none px-3 py-2 text-sm focus:ring-1 focus:ring-zinc-900 outline-none font-mono text-zinc-900 bg-zinc-50 focus:bg-white transition-colors"
              />
            </div>

            {/* Body */}
            <div>
              <label className="text-[10px] font-display font-bold text-zinc-500 mb-1.5 block uppercase tracking-widest">Body</label>
              <textarea
                value={item.email_body || ''}
                onChange={(e) => handleEdit(index, 'email_body', e.target.value)}
                onBlur={() => saveEdit(item)}
                rows={8}
                className="w-full border border-zinc-300 rounded-none px-3 py-3 text-sm font-mono leading-relaxed focus:ring-1 focus:ring-zinc-900 outline-none resize-y text-zinc-900 bg-zinc-50 focus:bg-white transition-colors"
              />
            </div>
          </div>
        ))}
        {items.length === 0 && (
          <div className="text-center py-16 bg-white border-2 border-dashed border-zinc-200 rounded-none text-zinc-500 font-mono text-sm">
            No emails to send.
          </div>
        )}
      </div>

      {/* Bottom controls */}
      <div className="flex gap-4 mt-8 mb-8 pt-6 border-t border-zinc-200">
        <button
          id="send-all-bottom-btn"
          onClick={handleSendAll}
          disabled={items.length === 0 || sendableCount === 0}
          className="w-full sm:w-auto px-8 py-3.5 bg-indigo-700 text-white font-display font-bold text-sm rounded-none hover:bg-indigo-800 disabled:bg-zinc-300 disabled:text-zinc-500 disabled:cursor-not-allowed transition-colors uppercase tracking-widest"
        >
          {sendableCount > 0 ? `Send ${sendableCount} Email${sendableCount !== 1 ? 's' : ''}` : 'No Emails to Send'}
        </button>
      </div>

    </div>
  )
}

// Pick a future local date-time; drafts start sending automatically then
function ScheduleButton({ campaignId, scheduledAt, onChange }) {
  const { toast } = useUI()
  const [open, setOpen] = useState(false)
  const [value, setValue] = useState('')

  const save = async () => {
    const when = new Date(value)
    if (!value || isNaN(when) || when <= new Date()) {
      toast.error('Pick a time in the future')
      return
    }
    try {
      const { data } = await api.post(`/campaign/${campaignId}/schedule`, { send_at: when.toISOString() })
      onChange(data.scheduled_at)
      setOpen(false)
      toast.success(`Scheduled for ${when.toLocaleString()}`)
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to schedule'))
    }
  }

  const cancel = async () => {
    try {
      await api.post(`/campaign/${campaignId}/schedule`, { send_at: null })
      onChange(null)
      toast.success('Schedule cancelled')
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to cancel schedule'))
    }
  }

  if (scheduledAt) {
    return (
      <div className="flex items-center gap-3 px-4 py-2 border border-indigo-200 bg-indigo-50 text-indigo-800 text-xs font-mono">
        🕒 Sends {formatDateTime(scheduledAt)}
        <button onClick={cancel} className="underline">cancel</button>
      </div>
    )
  }

  return open ? (
    <div className="flex flex-wrap items-center gap-2">
      <input
        type="datetime-local"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="border border-zinc-300 px-2 py-2 text-xs font-mono"
      />
      <button onClick={save} className="px-4 py-2.5 bg-zinc-900 text-white text-[10px] font-bold uppercase tracking-widest hover:bg-zinc-700">
        Schedule
      </button>
      <button onClick={() => setOpen(false)} className="text-xs text-zinc-500 hover:underline">cancel</button>
    </div>
  ) : (
    <button
      onClick={() => setOpen(true)}
      className="px-6 py-2.5 border border-indigo-300 text-indigo-800 bg-white font-display font-bold text-xs hover:bg-indigo-50 uppercase tracking-widest"
    >
      Schedule…
    </button>
  )
}
