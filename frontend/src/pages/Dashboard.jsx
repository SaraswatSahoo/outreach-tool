import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import api from '../api'
import { formatDate, parseLocalDate, parseServerDate } from '../utils/dates'
import usePoller from '../hooks/usePoller'
import { errorMessage, useUI } from '../context/UIContext'
import { REPLY_STATUSES } from '../constants/status'
import StatusBadge from '../components/StatusBadge'

export default function Dashboard() {
  const [stats, setStats] = useState(null)
  const [replyStats, setReplyStats] = useState(null)
  const [recent, setRecent] = useState([])
  const [reminders, setReminders] = useState([])
  const [recentReplies, setRecentReplies] = useState([])
  const [pendingSequence, setPendingSequence] = useState([])
  const [loading, setLoading] = useState(true)
  const [checking, setChecking] = useState(false)
  const [monitorResult, setMonitorResult] = useState(null)
  const poller = usePoller()
  const { toast } = useUI()

  const loadStats = useCallback(() => {
    return Promise.all([api.get('/dashboard'), api.get('/dashboard/reply-stats')])
      .then(([dashRes, replyRes]) => {
        setStats(dashRes.data.stats)
        setRecent(dashRes.data.recent_campaigns)
        setReminders(dashRes.data.reminders || [])
        setRecentReplies(dashRes.data.recent_replies || [])
        setPendingSequence(dashRes.data.pending_sequence_drafts || [])
        setReplyStats(replyRes.data)
      })
  }, [])

  const pollMonitor = useCallback(() => {
    setChecking(true)
    poller.start(async () => {
      const res = await api.get('/inbox/status')
      setMonitorResult(res.data)
      if (res.data.status === 'running') return false
      setChecking(false)
      loadStats().catch(() => {})
      return true
    }, 2000, () => setChecking(false))
  }, [poller, loadStats])

  useEffect(() => {
    Promise.all([loadStats(), api.get('/inbox/status')])
      .then(([, inboxRes]) => {
        setMonitorResult(inboxRes.data)
        if (inboxRes.data?.status === 'running') pollMonitor()
      })
      .catch((err) => toast.error(errorMessage(err, 'Failed to load dashboard')))
      .finally(() => setLoading(false))
  }, [loadStats, pollMonitor, toast])

  const handleCheckNow = async () => {
    setChecking(true)
    try {
      const res = await api.post('/inbox/check')
      setMonitorResult(res.data)
      pollMonitor()
    } catch (err) {
      if (err.response?.status === 429) {
        // Monitor is already running in background, poll for results
        pollMonitor()
      } else {
        setMonitorResult({ error: errorMessage(err, 'Check failed.') })
        setChecking(false)
      }
    }
  }

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading dashboard...</div>
  }

  return (
    <div id="dashboard-page">
      <h1 className="text-3xl font-display font-bold text-zinc-950 mb-8 tracking-tight">Dashboard</h1>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatCard
          id="stat-campaigns"
          label="Total Campaigns"
          value={stats?.total_campaigns ?? 0}
          color="indigo"
        />
        <StatCard
          id="stat-sent"
          label="Emails Sent"
          value={stats?.total_sent ?? 0}
          color="green"
        />
        <StatCard
          id="stat-failed"
          label="Failed"
          value={stats?.total_failed ?? 0}
          color="red"
        />
        <StatCard
          id="stat-followups"
          label="Follow-ups Sent"
          value={stats?.total_followups_sent ?? 0}
          color="amber"
        />
      </div>

      {/* Quick Actions */}
      <div className="mb-8">
        <Link
          to="/campaign/new"
          id="new-campaign-btn"
          className="inline-flex items-center px-6 py-2.5 bg-indigo-700 text-white text-sm font-medium hover:bg-indigo-800 transition-colors rounded-none"
        >
          + New Campaign
        </Link>
      </div>

      {/* Follow-ups drafted by automatic sequences, waiting for review */}
      {pendingSequence.length > 0 && (
        <div className="mb-8 p-5 bg-indigo-50 border border-indigo-200">
          <h2 className="font-display font-semibold text-indigo-900 mb-2">✉️ Automatic follow-ups ready for review</h2>
          <ul className="space-y-1 text-sm">
            {pendingSequence.map((p) => (
              <li key={p.campaign_id}>
                <Link to={`/campaign/${p.campaign_id}/followup/preview`} className="text-indigo-800 hover:underline">
                  {p.count} draft{p.count !== 1 ? 's' : ''} in {p.campaign_name} →
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Latest replies detected in the inbox */}
      {recentReplies.length > 0 && (
        <div className="mb-8 p-5 bg-white border border-zinc-200 border-l-4 border-l-emerald-500">
          <h2 className="font-display font-semibold text-zinc-950 mb-3">💬 Recent replies</h2>
          <ul className="divide-y divide-zinc-100">
            {recentReplies.map((r) => (
              <li key={r.id} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                <StatusBadge status={r.classification === 'other' ? 'no_reply' : r.classification} />
                <span className="text-zinc-900">{r.name || r.from_email}</span>
                {r.summary && <span className="text-zinc-500 italic text-xs">{r.summary}</span>}
                <Link to={`/campaign/${r.campaign_id}`} className="text-indigo-700 hover:underline text-xs ml-auto">
                  {r.campaign_name} · {formatDate(r.received_at)} →
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Check-back reminders across all campaigns */}
      {reminders.length > 0 && (
        <div className="mb-8 p-5 bg-yellow-50 border border-yellow-200">
          <h2 className="font-display font-semibold text-yellow-900 mb-3">
            ⏰ {reminders.length} check-back{reminders.length !== 1 ? 's' : ''} due this week
          </h2>
          <ul className="space-y-1.5 text-sm">
            {reminders.map((r) => {
              const due = parseLocalDate(r.check_back_date)
              const overdue = due && due < new Date(new Date().toDateString())
              return (
                <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span className={`font-mono text-xs px-1.5 py-0.5 ${overdue ? 'bg-red-100 text-red-800' : 'bg-yellow-100 text-yellow-800'}`}>
                    {overdue ? 'OVERDUE' : 'DUE'} {due?.toLocaleDateString()}
                  </span>
                  <span className="text-zinc-900">{r.name ? `${r.name} · ` : ''}{r.email}</span>
                  <Link to={`/campaign/${r.campaign_id}`} className="text-indigo-700 hover:underline text-xs">
                    {r.campaign_name} →
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {/* Inbox Monitor */}
      <div className="mb-8 p-5 bg-white border border-zinc-200 rounded-none border-l-4 border-l-zinc-800">
        <div className="flex justify-between items-start">
          <div>
            <h2 className="text-lg font-display font-semibold text-zinc-950 mb-1 flex items-center gap-2">
              <span className="text-zinc-400 font-mono text-xs border border-zinc-200 px-1 py-0.5 leading-none">SYS</span> Inbox Monitor
            </h2>
            <p className="text-sm text-zinc-500 font-mono mt-2">
              {monitorResult?.last_run 
                ? `Last checked: ${Math.max(0, Math.floor((new Date() - parseServerDate(monitorResult.last_run)) / 60000))} minutes ago`
                : "Monitor has not run yet. Click Check Now to scan your inbox."}
            </p>
            <p className="text-xs text-zinc-400 font-mono mt-1">Status: Auto-check [ACTIVE, {monitorResult?.interval_minutes ?? 10}m]</p>
            
            <div className="mt-4 text-sm text-zinc-700 border-l-2 border-zinc-200 pl-4 font-mono">
              {monitorResult?.error ? (
                <p className="text-red-600 font-medium">{monitorResult.error}</p>
              ) : monitorResult && (monitorResult.bounces_detected > 0 || monitorResult.ooo_detected > 0 || monitorResult.replies_detected > 0) ? (
                <>
                  <p className="text-zinc-500 uppercase text-xs mb-1">Last run results</p>
                  <ul className="space-y-1">
                    {monitorResult.bounces_detected > 0 && <li>[!] {monitorResult.bounces_detected} hard bounce(s) detected → marked invalid</li>}
                    {monitorResult.ooo_detected > 0 && <li>[*] {monitorResult.ooo_detected} out-of-office repl{monitorResult.ooo_detected === 1 ? 'y' : 'ies'} → check back</li>}
                    {monitorResult.replies_detected > 0 && <li>[»] {monitorResult.replies_detected} new repl{monitorResult.replies_detected === 1 ? 'y' : 'ies'} recorded</li>}
                    <li>[+] {monitorResult.updated} recipients updated</li>
                  </ul>
                </>
              ) : monitorResult?.last_run ? (
                <p className="text-green-700">[✓] No new replies, bounces or out-of-office messages.</p>
              ) : null}
            </div>
          </div>
          <button
            onClick={handleCheckNow}
            disabled={checking}
            className="inline-flex justify-center items-center px-4 py-2 border border-zinc-200 bg-zinc-50 text-zinc-900 text-sm font-medium hover:bg-zinc-100 transition-colors disabled:opacity-50 min-w-[120px] rounded-none shadow-sm"
          >
            {checking ? (
              <span className="flex items-center gap-2">
                <span className="animate-spin h-4 w-4 border-2 border-gray-500 border-t-transparent rounded-full" />
                Scanning...
              </span>
            ) : "Check Now"}
          </button>
        </div>
      </div>

      {/* CRM Stats */}
      {replyStats && (
        <div className="mb-8 p-6 bg-white border border-zinc-200 rounded-none">
          <h2 className="text-xs font-display font-semibold text-zinc-500 mb-5 uppercase tracking-wider">Global Reply CRM Snapshot</h2>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4">
            {REPLY_STATUSES.map((st) => (
              <StatCard key={st.value} label={st.label} value={replyStats[st.value] ?? 0} color={STATUS_CARD_COLORS[st.value]} />
            ))}
          </div>
        </div>
      )}

      {/* Recent Campaigns */}
      <div>
        <h2 className="text-xs font-display font-semibold text-zinc-500 mb-3 uppercase tracking-wider">Recent Campaigns</h2>
        {recent.length === 0 ? (
          <p className="text-zinc-500 text-sm">No campaigns yet. Create your first one!</p>
        ) : (
          <div className="bg-white border border-zinc-200 divide-y divide-zinc-200 rounded-none">
            {recent.map((c) => (
              <Link
                key={c.id}
                to={`/campaign/${c.id}`}
                className="block px-5 py-4 hover:bg-zinc-50 transition-colors group"
              >
                <div className="flex justify-between items-center">
                  <span className="font-medium text-zinc-950 group-hover:text-indigo-700 transition-colors">{c.name}</span>
                  <span className="text-sm text-zinc-500 font-mono">
                    {c.recipient_count} recipients
                  </span>
                </div>
                <div className="text-xs text-zinc-400 mt-1 font-mono">
                  {formatDate(c.created_at)}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const STATUS_CARD_COLORS = {
  no_reply: 'gray',
  check_back: 'blue',
  interested: 'green',
  no_openings: 'amber',
  interview_scheduled: 'indigo',
  final_rejection: 'red',
  invalid_email: 'red',
}

function StatCard({ id, label, value, color }) {
  const borderColors = {
    indigo: 'border-t-indigo-600',
    green: 'border-t-emerald-600',
    red: 'border-t-rose-600',
    amber: 'border-t-amber-500',
    blue: 'border-t-blue-600',
    gray: 'border-t-zinc-400',
  }

  return (
    <div id={id} className={`bg-white border border-zinc-200 p-5 rounded-none border-t-4 ${borderColors[color]} hover:bg-zinc-50 transition-colors`}>
      <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">{label}</div>
      <div className="text-4xl font-display font-semibold text-zinc-950 mt-3">{value}</div>
    </div>
  )
}
