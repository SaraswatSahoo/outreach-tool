import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import api from '../api'
import StatusBadge from './StatusBadge'
import { AUTO_EXCLUDED_STATUSES, REPLY_STATUSES, statusInfo } from '../constants/status'
import { domainOf, useBlockedDomains } from '../context/BlockedDomainsContext'
import { errorMessage, useUI } from '../context/UIContext'
import { formatDate } from '../utils/dates'

function InlineStatusEditor({ record, onSaveSuccess }) {
  const originalStatus = record.reply_status || 'no_reply'
  const originalReplyContent = record.reply_content || ''
  const originalCheckBackDate = record.check_back_date || ''

  const [status, setStatus] = useState(originalStatus)
  const [replyContent, setReplyContent] = useState(originalReplyContent)
  const [checkBackDate, setCheckBackDate] = useState(originalCheckBackDate)
  const [isSaving, setIsSaving] = useState(false)
  const [showSaved, setShowSaved] = useState(false)
  const { toast } = useUI()

  const isChanged =
    status !== originalStatus ||
    replyContent !== originalReplyContent ||
    checkBackDate !== originalCheckBackDate

  const handleSave = async () => {
    setIsSaving(true)
    try {
      const res = await api.patch(`/recipient/${record.id}/status`, {
        reply_status: status,
        reply_content: replyContent,
        check_back_date: checkBackDate,
      })
      setShowSaved(true)
      setTimeout(() => setShowSaved(false), 2000)
      if (onSaveSuccess) onSaveSuccess(res.data.recipient)
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to save status'))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="mb-3 p-3 bg-gray-50 border border-gray-200 rounded-md">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <label className="text-xs font-semibold text-gray-700">Reply status:</label>
        <select
          value={status}
          onChange={e => setStatus(e.target.value)}
          className={`text-xs border-gray-300 rounded shadow-sm focus:border-indigo-300 focus:ring focus:ring-indigo-200 focus:ring-opacity-50 py-1 pl-2 pr-6 ${statusInfo(status).badge}`}
        >
          {REPLY_STATUSES.map(({ value, label }) => (
            <option key={value} value={value}>{label}</option>
          ))}
        </select>
        {AUTO_EXCLUDED_STATUSES.includes(status) && (
          <span className="text-[10px] text-gray-500 italic">Excluded from future follow-ups</span>
        )}
      </div>

      <details className="group mb-3" open={!!originalReplyContent}>
        <summary className="text-xs font-medium text-indigo-600 hover:text-indigo-800 cursor-pointer list-none flex items-center">
          <span className="mr-1 transform transition-transform group-open:rotate-180">▼</span>
          Paste their reply
        </summary>
        <div className="mt-2">
          <textarea
            className="w-full text-xs p-2 border border-gray-300 rounded focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
            rows={3}
            placeholder="Paste the email reply here..."
            value={replyContent}
            onChange={(e) => setReplyContent(e.target.value)}
          />
        </div>
      </details>

      {status === 'check_back' && (
        <div className="flex items-center gap-2 mb-3">
          <label className="text-xs font-medium text-gray-700">Check-back date:</label>
          <input
            type="date"
            value={checkBackDate}
            onChange={(e) => setCheckBackDate(e.target.value)}
            className="text-xs border border-gray-300 rounded p-1 w-32 focus:border-indigo-500 outline-none"
          />
        </div>
      )}

      <div className="flex items-center gap-2 mt-2 pt-2 border-t border-gray-200">
        <button
          onClick={handleSave}
          disabled={!isChanged || isSaving}
          className="px-3 py-1 bg-indigo-600 text-white rounded text-xs font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isSaving ? 'Saving...' : 'Save Changes'}
        </button>
        {showSaved && <span className="text-xs text-green-600 font-medium">✓ Saved</span>}
      </div>
    </div>
  )
}

export default function ContactHistoryPanel({ email, isOpen, onClose, onStatusUpdated }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const { isBlocked, toggle } = useBlockedDomains()

  useEffect(() => {
    if (!isOpen || !email) return
    let active = true
    setLoading(true)
    setLoadError('')
    setData(null)

    api.get(`/contact/${encodeURIComponent(email)}`)
      .then((res) => { if (active) setData(res.data) })
      .catch((err) => { if (active) setLoadError(errorMessage(err, 'Failed to load contact history')) })
      .finally(() => { if (active) setLoading(false) })

    return () => { active = false }
  }, [isOpen, email])

  useEffect(() => {
    if (!isOpen) return
    const handleEsc = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', handleEsc)
    return () => window.removeEventListener('keydown', handleEsc)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const domain = domainOf(data?.email)
  const blocked = isBlocked(domain)

  return (
    <div className="fixed inset-0 z-50 overflow-hidden flex justify-end">
      {/* Backdrop — clicking it closes the drawer */}
      <div className="absolute inset-0 bg-gray-900 bg-opacity-30 transition-opacity" onClick={onClose} />

      <div
        role="dialog"
        aria-modal="true"
        aria-label="Contact history"
        className="relative w-full max-w-md bg-white h-full shadow-xl flex flex-col overflow-y-auto"
      >
        <div className="p-4 border-b border-gray-200 flex justify-between items-center bg-gray-50 sticky top-0 z-10">
          <h2 className="text-lg font-semibold text-gray-900">Contact History</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 focus:outline-none" aria-label="Close">
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
          </button>
        </div>

        {loading ? (
          <div className="p-6 text-center text-gray-500">Loading history...</div>
        ) : loadError ? (
          <div className="p-6 text-center text-red-600 text-sm">{loadError}</div>
        ) : data ? (
          <div className="p-6 space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xl">📧</span>
                  <span className="font-semibold text-gray-900 text-lg break-all">{data.email}</span>
                </div>
                {data.resolved_name ? (
                  <div className="flex items-center gap-2 text-sm text-gray-600 ml-1">
                    <span>👤</span>
                    <span>{data.resolved_name}</span>
                  </div>
                ) : null}
                {data.company ? (
                  <div className="flex items-center gap-2 text-sm text-gray-600 ml-1 mt-1">
                    <span>🏢</span>
                    <span>{data.company}</span>
                  </div>
                ) : null}

                <div className="mt-4 text-sm text-gray-500 bg-gray-50 p-2 rounded-md border border-gray-100">
                  Contacted <span className="font-semibold">{data.total_contacts}</span> time{data.total_contacts !== 1 ? 's' : ''} across campaigns
                </div>
              </div>

              {domain && (
                <button
                  onClick={() => toggle(domain)}
                  className={`shrink-0 text-xs px-2 py-1 mt-1 rounded border transition-all ${
                    blocked
                      ? 'bg-red-100 text-red-600 border-red-300 hover:bg-red-200'
                      : 'bg-gray-100 text-gray-500 border-gray-300 hover:bg-red-50 hover:text-red-400'
                  }`}
                  title={blocked ? 'Click to unblock this domain' : 'Block this company\'s domain'}
                >
                  {blocked ? '🚫 Blocked' : 'Block Domain'}
                </button>
              )}
            </div>

            {/* Campaign History list */}
            <div className="space-y-4">
              {data.history.map((record) => (
                <div key={record.id} className="border border-gray-200 rounded-lg p-3 shadow-sm bg-white">
                  <div className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-2 border-b border-gray-100 pb-1">
                    Campaign: {record.campaign_name}
                  </div>

                  <div className="flex justify-between items-center mb-2">
                    <span className="text-xs text-gray-500">
                      {record.sent_at ? `Sent: ${formatDate(record.sent_at)}` : 'Not sent yet'}
                    </span>
                    {record.status !== 'sent' && <StatusBadge status={record.status} />}
                  </div>

                  {record.status === 'sent' ? (
                    <InlineStatusEditor
                      key={`${record.id}-${record.reply_status}`}
                      record={record}
                      onSaveSuccess={(updated) => {
                        setData(prev => ({
                          ...prev,
                          history: prev.history.map(r => (r.id === updated.id ? { ...r, ...updated } : r)),
                        }))
                        if (onStatusUpdated) onStatusUpdated(updated.id, updated.reply_status)
                      }}
                    />
                  ) : null}

                  <div className="text-sm font-medium text-gray-900 mt-3 mb-2">
                    Subject: "{record.subject || '—'}"
                  </div>

                  <div className="flex gap-4 text-xs mt-3">
                    <details className="group cursor-pointer">
                      <summary className="text-indigo-600 font-medium hover:text-indigo-800 list-none flex items-center">
                        <span className="mr-1 transform transition-transform group-open:rotate-180">▼</span>
                        Email Body
                      </summary>
                      <div className="mt-2 p-3 bg-gray-50 text-gray-700 rounded border border-gray-200 font-mono text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">
                        {record.email_body}
                      </div>
                    </details>

                    {record.followups?.map((fu) => (
                      <details key={fu.id} className="group cursor-pointer">
                        <summary className="text-indigo-600 font-medium hover:text-indigo-800 list-none flex items-center">
                          <span className="mr-1 transform transition-transform group-open:rotate-180">▼</span>
                          Follow-up #{fu.step || 1}
                        </summary>
                        <div className="mt-2 p-3 bg-gray-50 text-gray-700 rounded border border-gray-200 font-mono text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto">
                          <div className="text-gray-400 mb-2 italic">
                            Sent: {formatDate(fu.sent_at, 'not yet')}{fu.source === 'sequence' ? ' · automatic' : ''}
                          </div>
                          {fu.email_body}
                        </div>
                      </details>
                    ))}
                  </div>

                  {record.replies?.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {record.replies.map((rep) => (
                        <div key={rep.id} className="p-2 bg-emerald-50 border border-emerald-200 rounded text-xs">
                          <div className="flex flex-wrap items-center gap-2 text-gray-500 mb-1">
                            <span className="font-medium text-emerald-800">💬 Reply · {formatDate(rep.received_at)}</span>
                            {rep.summary && <span className="italic">{rep.summary}</span>}
                          </div>
                          <div className="font-mono text-[11px] text-gray-700 whitespace-pre-wrap max-h-40 overflow-y-auto">{rep.body}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="mt-3 text-xs flex justify-between items-center border-t border-gray-100 pt-2">
                    <span className="text-gray-500">
                      Follow-ups sent: <span className={record.follow_up_sent ? "text-green-600 font-medium" : "text-gray-400"}>{record.follow_up_sent || 0}</span>
                    </span>
                    <Link to={`/campaign/${record.campaign_id}`} onClick={onClose} className="text-indigo-600 hover:text-indigo-800 font-medium">
                      Go to Campaign →
                    </Link>
                  </div>
                </div>
              ))}
            </div>

            {/* Same Domain Contacts */}
            {data.same_domain_contacts?.length > 0 ? (
              <div className="mt-8 pt-4 border-t border-gray-200">
                <div className="text-sm font-semibold uppercase tracking-wider text-gray-500 mb-3 block">
                  Same Domain: {data.domain}
                </div>
                <div className="space-y-2">
                  {data.same_domain_contacts.map((sdc, idx) => (
                    <div key={idx} className="text-xs bg-gray-50 p-2 rounded border border-gray-100 flex flex-col gap-1">
                      <div className="font-medium text-gray-800">{sdc.email}</div>
                      <div className="flex justify-between text-gray-500">
                        <span>{sdc.campaign_name}</span>
                        <StatusBadge status={sdc.sent_at ? sdc.reply_status || 'no_reply' : 'draft'} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="p-6 text-center text-gray-500">No contact history available.</div>
        )}
      </div>
    </div>
  )
}
