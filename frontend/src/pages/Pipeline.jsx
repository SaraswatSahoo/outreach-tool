import { useEffect, useMemo, useState } from 'react'
import api from '../api'
import ContactHistoryPanel from '../components/ContactHistoryPanel'
import { REPLY_STATUSES } from '../constants/status'
import { errorMessage, useUI } from '../context/UIContext'
import { formatDate, parseLocalDate } from '../utils/dates'

// Kanban board of everyone you've emailed, one column per reply status.
// Drag a card (or use its menu on touch screens) to change the status.
export default function Pipeline() {
  const [cards, setCards] = useState([])
  const [campaigns, setCampaigns] = useState([])
  const [campaignFilter, setCampaignFilter] = useState('')
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(true)
  const [dragOver, setDragOver] = useState(null)
  const [selectedEmail, setSelectedEmail] = useState(null)
  const { toast } = useUI()

  useEffect(() => {
    api.get('/campaigns').then((res) => setCampaigns(res.data)).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    api.get('/pipeline', { params: campaignFilter ? { campaign_id: campaignFilter } : {} })
      .then((res) => setCards(res.data.recipients))
      .catch((err) => toast.error(errorMessage(err, 'Failed to load pipeline')))
      .finally(() => setLoading(false))
  }, [campaignFilter, toast])

  const columns = useMemo(() => {
    const q = text.trim().toLowerCase()
    const visible = q
      ? cards.filter((c) => [c.email, c.name, c.company, c.campaign_name].some((v) => v?.toLowerCase().includes(q)))
      : cards
    const byStatus = Object.fromEntries(REPLY_STATUSES.map((s) => [s.value, []]))
    for (const c of visible) (byStatus[c.reply_status] || byStatus.no_reply).push(c)
    return byStatus
  }, [cards, text])

  const moveCard = async (id, status) => {
    const card = cards.find((c) => c.id === id)
    if (!card || card.reply_status === status) return
    const previous = card.reply_status
    setCards((prev) => prev.map((c) => (c.id === id ? { ...c, reply_status: status } : c)))
    try {
      await api.patch(`/recipient/${id}/status`, { reply_status: status })
    } catch (err) {
      setCards((prev) => prev.map((c) => (c.id === id ? { ...c, reply_status: previous } : c)))
      toast.error(errorMessage(err, 'Failed to update status'))
    }
  }

  return (
    <div id="pipeline-page">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6 pb-4 border-b border-zinc-200">
        <div>
          <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">Pipeline</h1>
          <p className="text-xs font-mono text-zinc-500 mt-1">Drag contacts between columns to update their status.</p>
        </div>
        <div className="flex flex-wrap gap-3">
          <input
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Filter by name, email, company…"
            className="border border-zinc-300 bg-zinc-50 px-3 py-2 text-sm font-mono focus:bg-white outline-none focus:ring-1 focus:ring-zinc-900"
          />
          <select
            value={campaignFilter}
            onChange={(e) => setCampaignFilter(e.target.value)}
            className="border border-zinc-300 bg-zinc-50 px-2 py-2 text-xs font-mono max-w-xs"
          >
            <option value="">All campaigns</option>
            {campaigns.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">Loading pipeline...</div>
      ) : cards.length === 0 ? (
        <div className="text-center py-16 font-mono text-sm text-zinc-500 border border-dashed border-zinc-300">
          No sent emails yet. Contacts appear here once their first email is sent.
        </div>
      ) : (
        <div className="flex gap-3 overflow-x-auto pb-4 -mx-4 px-4 snap-x">
          {REPLY_STATUSES.map((status) => (
            <div
              key={status.value}
              onDragOver={(e) => { e.preventDefault(); setDragOver(status.value) }}
              onDragLeave={() => setDragOver((d) => (d === status.value ? null : d))}
              onDrop={(e) => {
                e.preventDefault()
                setDragOver(null)
                moveCard(Number(e.dataTransfer.getData('text/plain')), status.value)
              }}
              className={`snap-start shrink-0 w-64 flex flex-col border bg-zinc-100/60 transition-colors ${
                dragOver === status.value ? 'border-indigo-400 bg-indigo-50' : 'border-zinc-200'
              }`}
            >
              <div className="px-3 py-2 border-b border-zinc-200 flex items-center justify-between bg-white">
                <span className="flex items-center gap-2 text-xs font-semibold text-zinc-700 uppercase tracking-wider">
                  <span className={`w-2 h-2 rounded-full ${status.dot}`} />
                  {status.label}
                </span>
                <span className="text-xs font-mono text-zinc-400">{columns[status.value].length}</span>
              </div>
              <div className="p-2 space-y-2 min-h-[8rem] max-h-[70vh] overflow-y-auto">
                {columns[status.value].map((c) => (
                  <PipelineCard key={c.id} card={c} onOpen={() => setSelectedEmail(c.email)} onMove={moveCard} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <ContactHistoryPanel
        email={selectedEmail}
        isOpen={!!selectedEmail}
        onClose={() => setSelectedEmail(null)}
        onStatusUpdated={(id, status) => setCards((prev) => prev.map((c) => (c.id === id ? { ...c, reply_status: status } : c)))}
      />
    </div>
  )
}

function PipelineCard({ card, onOpen, onMove }) {
  const due = card.reply_status === 'check_back' ? parseLocalDate(card.check_back_date) : null
  const overdue = due && due < new Date(new Date().toDateString())

  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData('text/plain', String(card.id))
        e.dataTransfer.effectAllowed = 'move'
      }}
      onClick={onOpen}
      className="bg-white border border-zinc-200 p-2.5 text-xs cursor-grab active:cursor-grabbing hover:border-zinc-400 shadow-sm"
    >
      <div className="font-medium text-zinc-900 truncate">{card.name || card.email}</div>
      {card.name && <div className="font-mono text-zinc-500 truncate">{card.email}</div>}
      {(card.company || card.role) && (
        <div className="text-zinc-600 truncate">{[card.role, card.company].filter(Boolean).join(' · ')}</div>
      )}
      <div className="text-zinc-400 truncate mt-1">{card.campaign_name}</div>
      <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
        <span className="font-mono text-[10px] text-zinc-500">sent {formatDate(card.sent_at)}</span>
        {card.follow_up_sent > 0 && (
          <span className="font-mono text-[10px] px-1 bg-amber-50 border border-amber-200 text-amber-800">{card.follow_up_sent} f/up</span>
        )}
        {card.replied_at && (
          <span className="font-mono text-[10px] px-1 bg-emerald-50 border border-emerald-200 text-emerald-800">replied</span>
        )}
        {due && (
          <span className={`font-mono text-[10px] px-1 border ${overdue ? 'bg-red-50 border-red-200 text-red-800' : 'bg-blue-50 border-blue-200 text-blue-800'}`}>
            {overdue ? 'overdue' : 'due'} {due.toLocaleDateString()}
          </span>
        )}
      </div>
      {/* Touch-friendly alternative to drag and drop */}
      <select
        value={card.reply_status}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => onMove(card.id, e.target.value)}
        className="mt-2 w-full border border-zinc-200 bg-zinc-50 text-[11px] py-0.5 lg:hidden"
        aria-label="Change status"
      >
        {REPLY_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
      </select>
    </div>
  )
}
