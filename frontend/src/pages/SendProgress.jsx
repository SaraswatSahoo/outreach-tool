import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import api from '../api'
import ProgressBar from '../components/ProgressBar'
import usePoller from '../hooks/usePoller'
import { errorMessage, useUI } from '../context/UIContext'
import { formatDateTime } from '../utils/dates'

export default function SendProgress({ isFollowUp = false }) {
  const { campaignId } = useParams()
  const navigate = useNavigate()
  const [progress, setProgress] = useState({
    current: 0,
    total: 0,
    status: 'idle',
    log: [],
    current_email: '',
  })
  const [connectionLost, setConnectionLost] = useState(false)
  const [stopping, setStopping] = useState(false)
  const poller = usePoller()
  const { toast, confirm } = useUI()
  const base = isFollowUp ? `/campaign/${campaignId}/followup` : `/campaign/${campaignId}`

  useEffect(() => {
    const pollUrl = isFollowUp
      ? `/campaign/${campaignId}/followup/progress`
      : `/campaign/${campaignId}/progress`

    poller.start(async () => {
      const res = await api.get(pollUrl)
      setProgress(res.data)
      setConnectionLost(false)
      // Anything but 'sending' is final (complete / error / interrupted / idle)
      return res.data.status !== 'sending'
    }, 3000, () => setConnectionLost(true))
  }, [campaignId, isFollowUp, poller])

  const sentCount = progress.log.filter((l) => l.status === 'sent').length
  const failedCount = progress.log.filter((l) => l.status === 'failed').length
  const skippedCount = progress.log.filter((l) => l.status === 'skipped').length
  const notSentCount = progress.log.filter((l) => l.status === 'not_sent').length

  const handleStop = async () => {
    const ok = await confirm({
      title: 'Stop sending?',
      message: 'Sending stops after the current email. Unsent emails stay as drafts and can be sent later.',
      confirmLabel: 'Stop',
      danger: true,
    })
    if (!ok) return
    setStopping(true)
    try {
      await api.post(`${base}/send/stop`)
      toast.info('Stopping after the current email…')
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to stop'))
      setStopping(false)
    }
  }

  // Estimate time remaining
  const remaining = progress.total - progress.current
  const etaSeconds = remaining * (progress.delay_seconds || 60)
  const etaMinutes = Math.ceil(etaSeconds / 60)

  return (
    <div id="send-progress-page" className="max-w-2xl mx-auto pb-12">
      <div className="mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">
          {isFollowUp ? 'Sending Follow-ups' : 'Sending Emails'}
        </h1>
      </div>

      {/* Progress Bar */}
      <div className="bg-white border border-zinc-200 p-8 mb-8">
        <ProgressBar
          current={progress.current}
          total={progress.total}
          label="Sending progress"
        />

        {progress.status === 'sending' ? (
          <div className="mt-6 text-sm font-mono text-zinc-600">
            {progress.waiting_until ? (
              <p className="text-amber-800 bg-amber-50 border border-amber-200 p-3">
                ⏸ Waiting — {progress.waiting_reason}. Resumes {formatDateTime(progress.waiting_until)}.
                <span className="block text-[11px] text-amber-700 mt-1">
                  Keep the backend running; change sending hours or the daily limit in Settings.
                </span>
              </p>
            ) : (
              <p>
                CURRENTLY SENDING TO:{' '}
                <span className="font-bold text-indigo-700 bg-indigo-50 px-2 py-0.5 border border-indigo-200 ml-2">
                  {progress.current_email || '...'}
                </span>
              </p>
            )}
            {remaining > 0 && !progress.waiting_until ? (
              <p className="mt-2 text-zinc-400 text-[10px] uppercase tracking-widest">
                ~{etaMinutes} minute{etaMinutes !== 1 ? 's' : ''} remaining
              </p>
            ) : null}
          </div>
        ) : null}

        {progress.status === 'idle' ? (
          <p className="mt-6 text-sm font-mono text-zinc-500">
            No active send job for this campaign. It may have finished before the server restarted.
          </p>
        ) : null}

        {connectionLost ? (
          <p className="mt-6 text-sm font-mono text-red-700">Lost connection to the server. Reload to check progress.</p>
        ) : null}

        {progress.status === 'sending' ? (
          <button
            onClick={handleStop}
            disabled={stopping || progress.cancel_requested}
            className="mt-6 px-4 py-2 border border-red-300 text-red-700 bg-white text-[10px] font-bold uppercase tracking-widest hover:bg-red-50 disabled:opacity-50"
          >
            {stopping || progress.cancel_requested ? 'Stopping…' : 'Stop sending'}
          </button>
        ) : null}

        {progress.status === 'complete' && notSentCount > 0 ? (
          <p className="mt-6 text-sm font-mono text-amber-800 bg-amber-50 border border-amber-200 p-3">
            {notSentCount} email{notSentCount !== 1 ? 's' : ''} couldn't reach Gmail and {notSentCount !== 1 ? 'are' : 'is'} still
            a draft. Check your connection in Settings → Test Gmail connection, then send the campaign's drafts again.
          </p>
        ) : null}

        {progress.status === 'stopped' ? (
          <p className="mt-6 text-sm font-mono text-zinc-700 bg-zinc-50 border border-zinc-200 p-3">
            Sending was stopped. Emails that weren't sent are still drafts.
          </p>
        ) : null}

        {progress.status === 'interrupted' ? (
          <p className="mt-6 text-sm font-mono text-amber-800 bg-amber-50 border border-amber-200 p-3">
            The server restarted while sending. Emails that weren't sent are still drafts and can be sent again from the campaign page.
          </p>
        ) : null}

        {progress.status === 'error' ? (
          <p className="mt-6 text-sm font-mono text-red-700 bg-red-50 border border-red-200 p-3">
            {progress.stop_reason || 'Sending stopped early — see the log below. Unsent emails are still drafts and can be sent again.'}
          </p>
        ) : null}

        {progress.status !== 'sending' ? (
          <div className="mt-6">
            <div className="flex flex-wrap gap-4 font-mono text-sm mb-6 border border-zinc-200 p-4 bg-zinc-50">
              <span className="text-emerald-700 font-bold flex items-center gap-2">
                <span className="bg-emerald-100 text-emerald-800 px-1.5 py-0.5">✓</span> {sentCount} SENT
              </span>
              {failedCount > 0 ? (
                <span className="text-red-700 font-bold flex items-center gap-2">
                  <span className="bg-red-100 text-red-800 px-1.5 py-0.5">✗</span> {failedCount} FAILED
                </span>
              ) : null}
              {notSentCount > 0 ? (
                <span className="text-amber-700 font-bold flex items-center gap-2" title="Gmail couldn't be reached — these are still drafts">
                  <span className="bg-amber-100 text-amber-800 px-1.5 py-0.5">⏸</span> {notSentCount} NOT SENT (STILL DRAFTS)
                </span>
              ) : null}
              {skippedCount > 0 ? (
                <span className="text-zinc-600 font-bold flex items-center gap-2">
                  <span className="bg-zinc-100 text-zinc-700 px-1.5 py-0.5">↷</span> {skippedCount} SKIPPED
                </span>
              ) : null}
            </div>
            <button
              id="view-campaign-btn"
              onClick={() => navigate(`/campaign/${campaignId}`)}
              className="px-6 py-3 bg-indigo-700 text-white font-display font-bold uppercase tracking-widest text-xs rounded-none hover:bg-indigo-800 transition-colors w-full sm:w-auto"
            >
              View Campaign Details
            </button>
          </div>
        ) : null}
      </div>

      {/* Activity Log */}
      <div>
        <h2 className="text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-3">Activity Log</h2>
        {progress.log.length === 0 ? (
          <p className="text-zinc-500 font-mono text-xs border border-dashed border-zinc-300 p-4 text-center bg-zinc-50 uppercase tracking-widest">
            Waiting for first email to send...
          </p>
        ) : (
          <div className="bg-white border border-zinc-200 divide-y divide-zinc-200 max-h-80 overflow-y-auto font-mono text-xs">
            {[...progress.log].reverse().map((entry, i) => (
              <div key={i} className="px-4 py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2 hover:bg-zinc-50 transition-colors">
                <div className="flex items-center gap-3">
                  <span className={`w-1.5 h-1.5 rounded-none ${
                    entry.status === 'sent' ? 'bg-emerald-500'
                      : entry.status === 'skipped' ? 'bg-zinc-400'
                        : entry.status === 'not_sent' ? 'bg-amber-500' : 'bg-red-500'
                  }`} />
                  <span className="text-zinc-900 font-bold">{entry.email}</span>
                </div>
                <div className={`text-[10px] uppercase tracking-widest ${
                  entry.status === 'sent' ? 'text-emerald-600'
                    : entry.status === 'skipped' ? 'text-zinc-500'
                      : entry.status === 'not_sent' ? 'text-amber-700' : 'text-red-600'
                }`}>
                  {entry.status === 'sent' ? 'SENT'
                    : entry.status === 'skipped' ? `SKIPPED: ${entry.error}`
                      : entry.status === 'not_sent' ? `NOT SENT (STILL A DRAFT): ${entry.error}`
                        : `FAILED: ${entry.error || 'UNKNOWN ERROR'}`}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
