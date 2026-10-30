import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import api from '../api'
import { formatDateTime, parseLocalDate } from '../utils/dates'
import { REPLY_STATUSES, AUTO_EXCLUDED_STATUSES, FOLLOWUP_INELIGIBLE_STATUSES, statusInfo } from '../constants/status'
import GenerationProgress from '../components/GenerationProgress'
import StatusBadge from '../components/StatusBadge'
import usePoller from '../hooks/usePoller'
import { errorMessage, useUI } from '../context/UIContext'

export default function CampaignDetail() {
  const { campaignId } = useParams()
  const navigate = useNavigate()
  const [campaign, setCampaign] = useState(null)
  const [bounces, setBounces] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [followupContext, setFollowupContext] = useState('')
  const [generationProgress, setGenerationProgress] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [replyExpanded, setReplyExpanded] = useState({})
  const [showDeliveryIssues, setShowDeliveryIssues] = useState(false)
  const poller = usePoller()
  const { toast, confirm } = useUI()

  const fetchCampaign = useCallback(() => {
    return Promise.all([
      api.get(`/campaign/${campaignId}`),
      api.get(`/campaign/${campaignId}/bounces`)
    ])
      .then(([campRes, bouncesRes]) => {
        setCampaign(campRes.data)
        setBounces(bouncesRes.data)
      })
      .catch((err) => setError(errorMessage(err, 'Failed to load campaign')))
      .finally(() => setLoading(false))
  }, [campaignId])

  useEffect(() => {
    fetchCampaign()
  }, [fetchCampaign])

  const toggleExpand = (id) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const toggleReplyExpand = (id) => {
    setReplyExpanded((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const updateRecipientLocally = (id, updates) => {
    setCampaign(prev => ({
      ...prev,
      recipients: prev.recipients.map(r => r.id === id ? { ...r, ...updates } : r)
    }))
  }

  const handleFollowUp = async () => {
    if (eligibleRecipients.length === 0) return
    if (pendingFollowups > 0) {
      const ok = await confirm({
        title: 'Replace follow-up drafts?',
        message: `${pendingFollowups} unsent follow-up draft(s) already exist. Generating again replaces them.`,
        confirmLabel: 'Regenerate',
      })
      if (!ok) return
    }
    setGenerationProgress({ total: eligibleRecipients.length, completed: 0, failed: 0, status: "generating", errors: [] })
    setError('')

    try {
      await api.post(`/campaign/${campaignId}/generate-followups`, {
        global_context: followupContext,
        recipient_ids: eligibleRecipients.map(r => r.id)
      })
      poller.start(async () => {
        const { data } = await api.get(`/campaign/${campaignId}/generate-followup-progress`)
        setGenerationProgress(data)
        if (data.status === "generating") return false
        if (data.status === "complete") navigate(`/campaign/${campaignId}/followup/preview`)
        return true
      }, 1500, () => setError('Lost connection while generating follow-ups'))
    } catch (err) {
      setError(errorMessage(err, 'Failed to generate follow-ups'))
      setGenerationProgress(null)
    }
  }

  const handleCancelSchedule = async () => {
    try {
      await api.post(`/campaign/${campaignId}/schedule`, { send_at: null })
      setCampaign((prev) => ({ ...prev, scheduled_at: null }))
      toast.success('Schedule cancelled')
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to cancel schedule'))
    }
  }

  const handleRetryFailed = async () => {
    try {
      const { data } = await api.post(`/campaign/${campaignId}/retry-failed`)
      toast.success(`${data.recipients_reset + data.followups_reset} failed email(s) moved back to drafts`)
      await fetchCampaign()
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to reset failed emails'))
    }
  }

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading campaign...</div>
  }

  if (!campaign) {
    return <div className="text-center py-12 text-red-500">{error || 'Campaign not found'}</div>
  }

  const recipients = campaign.recipients || []
  const maxFollowups = campaign.max_followups || 3
  
  // Calculate Summary Counts
  const summary = {
    no_reply: 0, check_back: 0, interested: 0, no_openings: 0, 
    interview_scheduled: 0, final_rejection: 0, invalid_email: 0, excluded: 0
  }
  
  const eligibleRecipients = []
  const reminders = []
  const now = new Date()
  const sevenDaysFromNow = new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000)

  let draftCount = 0
  let failedCount = 0
  let pendingFollowups = 0

  recipients.forEach(r => {
    if (r.status === 'draft') draftCount++
    if (r.status === 'failed') failedCount++
    pendingFollowups += (r.followups || []).filter(f => f.status === 'draft').length
    const status = r.reply_status || 'no_reply'
    if (status in summary) summary[status]++
    const isAutoExcluded = AUTO_EXCLUDED_STATUSES.includes(status)
    if (isAutoExcluded || r.exclude_followup) {
      summary.excluded++
    }

    // Must match the backend's eligibility rule (app.py FOLLOWUP_INELIGIBLE_STATUSES)
    if (r.status === 'sent' && (r.follow_up_sent || 0) < maxFollowups && !FOLLOWUP_INELIGIBLE_STATUSES.includes(status) && !r.exclude_followup) {
      eligibleRecipients.push(r)
    }

    if (status === 'check_back' && r.check_back_date) {
      const cbDate = parseLocalDate(r.check_back_date)
      if (cbDate <= sevenDaysFromNow && cbDate >= new Date(now.getTime() - 24 * 60 * 60 * 1000)) {
        reminders.push(r)
      }
    }
  })

  return (
    <div id="campaign-detail-page" className="max-w-4xl mx-auto">
      {/* Campaign Info */}
      <div className="mb-8 pb-6 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">{campaign.name}</h1>
        <p className="text-sm text-zinc-500 font-mono mt-2">
          Created {formatDateTime(campaign.created_at)}
        </p>
        <p className="text-sm text-zinc-700 mt-4 max-w-2xl">{campaign.goal}</p>
        {campaign.additional_context ? (
          <p className="text-sm text-zinc-500 mt-2 italic border-l-2 border-zinc-300 pl-3">{campaign.additional_context}</p>
        ) : null}
        {campaign.scheduled_at ? (
          <div className="mt-4 inline-flex flex-wrap items-center gap-3 text-xs font-mono bg-indigo-50 border border-indigo-200 text-indigo-800 px-3 py-2">
            <span>🕒 Scheduled to send {formatDateTime(campaign.scheduled_at)}</span>
            <button onClick={handleCancelSchedule} className="underline hover:text-indigo-950">Cancel schedule</button>
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 text-red-700 rounded-md text-sm">
          {error}
        </div>
      ) : null}

      {/* Pending work */}
      {(draftCount > 0 || failedCount > 0 || pendingFollowups > 0) && (
        <div className="mb-6 flex flex-wrap gap-3">
          {draftCount > 0 && (
            <Link
              to={`/campaign/${campaignId}/preview`}
              className="px-4 py-2 bg-indigo-700 text-white text-xs font-bold uppercase tracking-widest hover:bg-indigo-800"
            >
              Review &amp; send {draftCount} draft{draftCount !== 1 ? 's' : ''} →
            </Link>
          )}
          {pendingFollowups > 0 && (
            <Link
              to={`/campaign/${campaignId}/followup/preview`}
              className="px-4 py-2 bg-amber-600 text-white text-xs font-bold uppercase tracking-widest hover:bg-amber-700"
            >
              Review {pendingFollowups} follow-up draft{pendingFollowups !== 1 ? 's' : ''} →
            </Link>
          )}
          {failedCount > 0 && (
            <button
              onClick={handleRetryFailed}
              className="px-4 py-2 border border-red-300 bg-red-50 text-red-800 text-xs font-bold uppercase tracking-widest hover:bg-red-100"
            >
              Retry {failedCount} failed
            </button>
          )}
        </div>
      )}

      {/* Check back reminders */}
      {reminders.length > 0 && (
        <div className="mb-6 p-4 bg-yellow-50 border border-yellow-200 rounded-lg">
          <p className="font-semibold text-yellow-800 mb-2">⚠️ {reminders.length} recipient(s) asked you to follow up soon:</p>
          <ul className="list-disc pl-5 text-sm text-yellow-700">
            {reminders.map(r => (
              <li key={r.id}>{r.email} (Check back by {parseLocalDate(r.check_back_date).toLocaleDateString()})</li>
            ))}
          </ul>
        </div>
      )}

      {/* Follow-up Summary Bar */}
      <div className="bg-zinc-50 border border-zinc-200 border-l-4 border-l-amber-500 rounded-none p-5 mb-8">
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center mb-4">
          <div>
            <h3 className="font-display font-semibold text-zinc-950 mb-1 flex items-center gap-2">
              <span className="text-amber-600 font-mono text-xs border border-amber-200 bg-amber-50 px-1 py-0.5 leading-none">ACTION</span> Follow-up Summary
            </h3>
            <div className="text-sm text-zinc-600 flex gap-4 flex-wrap font-mono mt-3">
              <span>No Reply: <strong className="text-zinc-900">{summary.no_reply}</strong></span>
              <span>Check Back: <strong className="text-zinc-900">{summary.check_back}</strong></span>
              <span>Interested: <strong className="text-green-700">{summary.interested}</strong></span>
              <span>Excluded: <strong className="text-red-700">{summary.excluded}</strong></span>
            </div>
          </div>
        </div>
        
        {eligibleRecipients.length > 0 && (
          <div className="mt-2">
            <input
              type="text"
              value={followupContext}
              onChange={(e) => setFollowupContext(e.target.value)}
              placeholder="Any additional context for this follow-up batch? (optional)"
              className="w-full border border-zinc-300 rounded-none px-3 py-2 text-sm mb-3 focus:ring-1 focus:ring-zinc-900 outline-none bg-white font-mono placeholder-zinc-400"
            />
            {generationProgress ? (
              <GenerationProgress progress={generationProgress} runningLabel="⚡ Generating Follow-ups..." tone="amber">
                {generationProgress.status !== 'generating' && generationProgress.status !== 'complete' && (
                  <div className="mt-4 border-t border-amber-100 pt-3 flex gap-3">
                    <button
                      onClick={() => navigate(`/campaign/${campaignId}/followup/preview`)}
                      className="px-4 py-1.5 bg-amber-600 text-white rounded text-xs hover:bg-amber-700 font-medium"
                    >
                      Continue to Preview
                    </button>
                    <button
                      onClick={() => setGenerationProgress(null)}
                      className="px-4 py-1.5 border border-amber-300 text-amber-800 rounded text-xs hover:bg-amber-50 font-medium"
                    >
                      Dismiss
                    </button>
                  </div>
                )}
              </GenerationProgress>
            ) : (
              <button
                onClick={handleFollowUp}
                className="px-6 py-2 bg-amber-600 text-white text-sm font-medium rounded-none hover:bg-amber-700 transition-colors uppercase tracking-wide"
              >
                {`Generate Follow-ups for ${eligibleRecipients.length} eligible recipients`}
              </button>
            )}
          </div>
        )}
      </div>

      <SequenceSettings campaign={campaign} onSaved={(seq) => setCampaign((prev) => ({ ...prev, ...seq }))} />

      {/* Delivery Issues */}
      {bounces && bounces.length > 0 && (
        <div className="mb-6 border border-red-200 bg-red-50 rounded-lg p-4">
          <div className="flex justify-between items-center mb-2">
            <h3 className="font-semibold text-red-900">
              ⚠️ Delivery Issues ({bounces.length})
            </h3>
            <button
              onClick={() => setShowDeliveryIssues(!showDeliveryIssues)}
              className="text-xs text-red-700 hover:text-red-900 font-medium"
            >
              {showDeliveryIssues ? '[▲ Hide]' : '[▼ Show]'}
            </button>
          </div>
          {showDeliveryIssues && (
            <div className="mt-4">
               <p className="text-sm text-red-800 mb-3">
                These addresses hard-bounced — emails were not delivered. Valid email addresses do not exist or are unreachable. They have been permanently excluded from future follow-ups.
               </p>
               <div className="space-y-2">
                 {bounces.map(b => (
                   <div key={b.id} className="flex justify-between items-center bg-white/50 p-2 rounded border border-red-100">
                     <div className="flex items-center gap-3">
                       <span className="text-red-500 font-bold">✗</span>
                       <span className="text-gray-900 font-medium line-through decoration-red-400">{b.email}</span>
                       {b.name && <span className="text-xs text-gray-500">({b.name})</span>}
                     </div>
                     <div className="flex items-center gap-3">
                       <span className="text-xs text-gray-500">
                         detected {formatDateTime(b.status_updated_at, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
                       </span>
                       <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-medium">
                         {(b.error_message || '').includes('Hard bounce') ? 'Auto-detected' : 'Manual'}
                       </span>
                     </div>
                   </div>
                 ))}
               </div>
            </div>
          )}
        </div>
      )}

      {/* Recipients Table */}
      <h2 className="text-xs font-display font-semibold text-zinc-500 mb-3 uppercase tracking-wider">
        Recipients ({recipients.length})
      </h2>

      <div className="bg-white border border-zinc-200 rounded-none">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-zinc-50 border-b border-zinc-200">
                <th className="text-left px-5 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wider">Company / Email</th>
                <th className="text-center px-5 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wider">Reply Status</th>
                <th className="text-center px-5 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wider">Sending</th>
                <th className="text-right px-5 py-3 text-xs font-semibold text-zinc-500 uppercase tracking-wider"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-200">
              {recipients.map((r) => (
                <RecipientRow
                  key={r.id}
                  recipient={r}
                  isExpanded={expanded[r.id]}
                  isReplyExpanded={replyExpanded[r.id]}
                  onToggle={() => toggleExpand(r.id)}
                  onToggleReply={() => toggleReplyExpand(r.id)}
                  updateLocal={updateRecipientLocally}
                  maxFollowups={maxFollowups}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function RecipientRow({ recipient, isExpanded, isReplyExpanded, onToggle, onToggleReply, updateLocal, maxFollowups }) {
  const r = recipient
  const [saveFlash, setSaveFlash] = useState(false)
  const { toast } = useUI()
  
  // Flash state
  const showFlash = () => {
    setSaveFlash(true)
    setTimeout(() => setSaveFlash(false), 2000)
  }

  // Handle status select change
  const handleStatusChange = async (e) => {
    const newStatus = e.target.value
    const previous = r.reply_status
    updateLocal(r.id, { reply_status: newStatus })
    try {
      const res = await api.patch(`/recipient/${r.id}/status`, { reply_status: newStatus })
      // Backend may also set exclude_followup for terminal statuses
      if (res.data?.recipient) updateLocal(r.id, res.data.recipient)
      showFlash()
    } catch (err) {
      updateLocal(r.id, { reply_status: previous })
      toast.error(errorMessage(err, 'Failed to update status'))
    }
  }

  const statusColor = statusInfo(r.reply_status || 'no_reply').dot
  const isAutoExcluded = AUTO_EXCLUDED_STATUSES.includes(r.reply_status)

  return (
    <>
      <tr className="hover:bg-gray-50 transition-colors group">
        <td className="px-4 py-3 text-gray-900 border-r border-gray-100 border-dashed">
          <div className="font-medium flex items-center gap-2 flex-wrap">
            {r.name || '—'}
            {r.replies?.length > 0 && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 bg-emerald-50 border border-emerald-200 text-emerald-800" title="Reply detected in your inbox">
                💬 replied
              </span>
            )}
          </div>
          {(r.company || r.role) && (
            <div className="text-xs text-gray-500">{[r.role, r.company].filter(Boolean).join(' · ')}</div>
          )}
          <div className="text-gray-500 font-mono text-xs">{r.email}</div>
          <div className="flex gap-2 mt-1">
            <button
              onClick={onToggleReply}
              className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center"
            >
              <span className="mr-1">{isReplyExpanded ? '▲' : '▼'}</span> Paste Reply
            </button>
            <button
              onClick={onToggle}
              className="text-xs text-indigo-600 hover:text-indigo-800"
            >
              {isExpanded ? 'Hide' : 'Show'} Email
            </button>
          </div>
        </td>
        
        <td className="px-4 py-3 border-r border-gray-100 border-dashed">
          <div className="flex flex-col items-center gap-1">
            <div className="relative flex items-center">
              <div className={`absolute left-2 w-2 h-2 rounded-full ${statusColor} pointer-events-none`}></div>
              <select
                value={r.reply_status || 'no_reply'}
                onChange={handleStatusChange}
                className="pl-6 pr-6 py-1 text-xs bg-gray-50 border border-gray-200 rounded-md focus:ring-1 focus:ring-indigo-500 outline-none w-36 appearance-none cursor-pointer hover:bg-white"
              >
                {REPLY_STATUSES.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
              <div className="absolute right-2 pointer-events-none text-gray-500 text-[10px]">▼</div>
            </div>
            {saveFlash && <span className="text-[10px] text-green-600">Saved!</span>}
          </div>
        </td>

        <td className="px-4 py-3 text-center text-xs">
          <div className="flex flex-col gap-1 items-center">
            {r.status === 'sent' ? (
              <span className="text-gray-500">Orig: <span className="text-green-600 font-medium">✓</span></span>
            ) : (
              <span className="text-gray-400">Orig: {r.status}</span>
            )}
            
            <span className={r.follow_up_sent ? 'text-gray-500' : 'text-gray-400'}>
              F/Up: <span className={r.follow_up_sent ? 'text-green-600 font-medium' : ''}>{r.follow_up_sent || 0}/{maxFollowups}</span>
            </span>
          </div>
        </td>

        <td className="px-4 py-3 text-right">
            {/* removed standard buttons to move logic into column 1 */}
        </td>
      </tr>

      {/* Reply pasting panel */}
      {isReplyExpanded && (
        <tr>
          <td colSpan={4} className="px-4 py-3 bg-indigo-50 border-b border-gray-200">
            <ReplyPanel recipient={r} updateLocal={updateLocal} isAutoExcluded={isAutoExcluded} />
          </td>
        </tr>
      )}

      {/* Original email viewing panel */}
      {isExpanded ? (
        <tr>
          <td colSpan={4} className="px-4 py-3 bg-gray-50 border-b border-gray-200">
            <div className="text-xs text-gray-500 mb-1">Subject: <span className="text-gray-900">{r.subject}</span></div>
            <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono bg-white p-3 rounded border border-gray-200">
              {r.email_body}
            </pre>
            {r.error_message ? (
              <p className="mt-2 text-xs text-red-600">Error: {r.error_message}</p>
            ) : null}

            {/* Show follow-ups if any */}
            {r.followups?.length > 0 ? (
              <div className="mt-3 border-t border-gray-200 pt-3">
                <p className="text-xs font-medium text-gray-600 mb-2">Follow-up(s):</p>
                {r.followups.map((fu) => (
                  <div key={fu.id} className="mb-2 bg-amber-50 p-2 rounded border border-amber-200">
                    <div className="text-[10px] font-mono uppercase tracking-wider text-amber-700 mb-1">
                      Follow-up #{fu.step || 1}{fu.source === 'sequence' ? ' · automatic' : ''}
                    </div>
                    <div className="text-xs text-gray-500">Subject: <span className="text-gray-900">{fu.subject}</span></div>
                    <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono mt-1">
                      {fu.email_body}
                    </pre>
                    <div className="text-xs text-gray-400 mt-1">
                      Status: {fu.status} {fu.sent_at ? `| ${formatDateTime(fu.sent_at)}` : ''}
                      {fu.error_message ? ` | ${fu.error_message}` : ''}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}

            {/* Replies detected in the inbox */}
            {r.replies?.length > 0 ? (
              <div className="mt-3 border-t border-gray-200 pt-3">
                <p className="text-xs font-medium text-gray-600 mb-2">Their replies:</p>
                {r.replies.map((rep) => (
                  <div key={rep.id} className="mb-2 bg-emerald-50 p-2 rounded border border-emerald-200">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-gray-500 mb-1">
                      <span>{formatDateTime(rep.received_at)}</span>
                      <StatusBadge status={rep.classification === 'other' ? 'no_reply' : rep.classification} />
                      {rep.summary && <span className="italic">{rep.summary}</span>}
                    </div>
                    <pre className="text-xs text-gray-700 whitespace-pre-wrap font-mono">{rep.body}</pre>
                  </div>
                ))}
              </div>
            ) : null}
          </td>
        </tr>
      ) : null}
    </>
  )
}

function ReplyPanel({ recipient, updateLocal, isAutoExcluded }) {
  const { toast } = useUI()
  const [content, setContent] = useState(recipient.reply_content || '')
  const [date, setDate] = useState(recipient.check_back_date || '')
  const [include, setInclude] = useState(!recipient.exclude_followup)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const handleSave = async () => {
    setSaving(true)
    const updates = {
      reply_content: content,
      check_back_date: date || null,
      exclude_followup: include ? 0 : 1
    }
    
    try {
      await api.patch(`/recipient/${recipient.id}/status`, updates)
      updateLocal(recipient.id, updates)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to save reply'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="text-sm bg-white p-3 rounded border border-indigo-100 shadow-sm">
      <label className="block text-xs font-medium text-indigo-900 mb-1">Their Reply</label>
      <textarea
        className="w-full border border-gray-300 rounded px-2 py-2 text-xs focus:ring-1 focus:ring-indigo-500 outline-none mb-3"
        rows={3}
        placeholder="Paste the recruiter's reply here for smarter follow-up generation..."
        value={content}
        onChange={e => setContent(e.target.value)}
      />

      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <div className="flex items-center gap-4">
          {recipient.reply_status === 'check_back' && (
            <div className="flex items-center gap-2">
              <label className="text-xs text-gray-700">Check Back Date:</label>
              <input 
                type="date"
                value={date}
                onChange={e => setDate(e.target.value)}
                className="border border-gray-300 rounded px-2 py-1 text-xs"
              />
            </div>
          )}

          <div className="flex items-center gap-2">
            {isAutoExcluded ? (
              <span className="bg-gray-200 text-gray-500 text-xs px-2 py-1 rounded">Excluded from follow-ups</span>
            ) : (
              <label className="flex items-center gap-1 text-xs text-gray-700 cursor-pointer">
                <input 
                  type="checkbox" 
                  checked={include} 
                  onChange={e => setInclude(e.target.checked)}
                  className="rounded text-indigo-600"
                />
                Include in follow-up batch
              </label>
            )}
          </div>
        </div>

        <button 
          onClick={handleSave}
          disabled={saving}
          className="bg-indigo-600 text-white px-3 py-1 text-xs rounded hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : (saved ? '✓ Saved' : 'Save Reply Params')}
        </button>
      </div>
    </div>
  )
}


function SequenceSettings({ campaign, onSaved }) {
  const { toast } = useUI()
  const [enabled, setEnabled] = useState(!!campaign.sequence_enabled)
  const [autoSend, setAutoSend] = useState(!!campaign.auto_send_followups)
  const [steps, setSteps] = useState(campaign.sequence || [{ delay_days: 4 }, { delay_days: 7 }])
  const [saving, setSaving] = useState(false)
  const max = campaign.max_followups || 3

  const dirty =
    enabled !== !!campaign.sequence_enabled ||
    autoSend !== !!campaign.auto_send_followups ||
    JSON.stringify(steps) !== JSON.stringify(campaign.sequence)

  const setDelay = (i, value) =>
    setSteps((prev) => prev.map((s, j) => (j === i ? { delay_days: Math.max(1, Math.min(60, Number(value) || 1)) } : s)))

  const save = async () => {
    setSaving(true)
    try {
      const { data } = await api.put(`/campaign/${campaign.id}/sequence`, { enabled, auto_send: autoSend, steps })
      setSteps(data.sequence)
      onSaved({ sequence_enabled: data.sequence_enabled, auto_send_followups: data.auto_send_followups, sequence: data.sequence })
      toast.success(enabled ? 'Automatic follow-ups saved' : 'Automatic follow-ups turned off')
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to save sequence'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white border border-zinc-200 border-l-4 border-l-indigo-500 p-5 mb-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-display font-semibold text-zinc-950 flex items-center gap-2">
          <span className="text-indigo-600 font-mono text-xs border border-indigo-200 bg-indigo-50 px-1 py-0.5 leading-none">AUTO</span>
          Automatic Follow-up Sequence
        </h3>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="accent-indigo-700" />
          Enabled
        </label>
      </div>
      <p className="text-xs font-mono text-zinc-500 mt-2">
        For anyone still marked "No Reply", the next follow-up is drafted automatically once its delay has passed.
        Any detected reply — or a status you set — stops the sequence for that person.
      </p>

      {enabled && (
        <div className="mt-4 space-y-3">
          {steps.map((step, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-xs text-zinc-500 w-28">Follow-up #{i + 1}</span>
              <input
                type="number"
                min={1}
                max={60}
                value={step.delay_days}
                onChange={(e) => setDelay(i, e.target.value)}
                className="w-16 border border-zinc-300 px-2 py-1 text-sm font-mono"
              />
              <span className="text-zinc-600 text-xs">days after the {i === 0 ? 'original email' : 'previous follow-up'}</span>
              {steps.length > 1 && (
                <button onClick={() => setSteps((prev) => prev.filter((_, j) => j !== i))} className="text-xs text-red-600 hover:underline">
                  remove
                </button>
              )}
            </div>
          ))}
          {steps.length < max && (
            <button
              onClick={() => setSteps((prev) => [...prev, { delay_days: 7 }])}
              className="text-xs text-indigo-700 hover:underline"
            >
              + add step
            </button>
          )}
          <label className="flex items-start gap-2 text-sm cursor-pointer pt-2 border-t border-zinc-100">
            <input type="checkbox" checked={autoSend} onChange={(e) => setAutoSend(e.target.checked)} className="accent-indigo-700 mt-1" />
            <span>
              Send automatically
              <span className="block text-xs text-zinc-500">
                Off: drafts wait for your review in "Review follow-up drafts". On: they're sent without review
                (still within your sending hours and daily limit).
              </span>
            </span>
          </label>
        </div>
      )}

      {dirty && (
        <button
          onClick={save}
          disabled={saving}
          className="mt-4 px-4 py-2 bg-indigo-700 text-white text-xs font-bold uppercase tracking-widest hover:bg-indigo-800 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save sequence'}
        </button>
      )}
    </div>
  )
}
