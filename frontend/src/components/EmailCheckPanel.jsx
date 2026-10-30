import { useState } from 'react'

// Results of checking the email list before a campaign is created.
// The list itself only keeps good + risky addresses; removed ones live here
// and can be restored (or fixed, for typos) with one click.

const BADGE = {
  invalid: 'bg-red-100 text-red-800 border-red-200',
  risky: 'bg-amber-50 text-amber-800 border-amber-200',
}

function Entry({ entry, onRestore, onUseSuggestion, restoreLabel = 'Restore' }) {
  return (
    <li className="py-2 text-xs">
      <div className="font-mono text-zinc-900 break-all">{entry.email}</div>
      <div className={`inline-block mt-1 px-1.5 py-0.5 border ${BADGE[entry.verdict] || BADGE.risky}`}>{entry.reason}</div>
      <div className="flex flex-wrap gap-3 mt-1">
        {entry.suggestion && (
          <button type="button" onClick={() => onUseSuggestion(entry)} className="text-indigo-700 hover:underline">
            Use {entry.suggestion}
          </button>
        )}
        {onRestore && (
          <button type="button" onClick={() => onRestore(entry)} className="text-zinc-500 hover:underline">
            {restoreLabel}
          </button>
        )}
      </div>
    </li>
  )
}

// Side column: addresses taken out of the list
export function RemovedColumn({ removed, onRestore, onUseSuggestion }) {
  return (
    <div className="border border-zinc-200 bg-white h-full flex flex-col">
      <div className="px-3 py-2 border-b border-zinc-200 bg-zinc-50 text-[10px] font-display font-bold uppercase tracking-widest text-zinc-700">
        Removed ({removed.length})
      </div>
      {removed.length === 0 ? (
        <p className="p-3 text-[11px] font-mono text-zinc-400">
          Addresses that can't receive email will appear here, so they never become failed sends.
        </p>
      ) : (
        <ul className="px-3 divide-y divide-zinc-100 overflow-y-auto max-h-72">
          {removed.map((entry) => (
            <Entry key={entry.email} entry={entry} onRestore={onRestore} onUseSuggestion={onUseSuggestion} />
          ))}
        </ul>
      )}
    </div>
  )
}

// Under the list: progress, the one-line summary, and the kept-but-risky addresses
export function CheckSummary({ checking, progress, stats, risky, onUseSuggestion, provider }) {
  const [showRisky, setShowRisky] = useState(false)

  if (checking) {
    const pct = progress?.total ? Math.round((progress.done / progress.total) * 100) : 0
    return (
      <div className="mt-3 border border-zinc-200 bg-white px-4 py-3">
        <div className="flex justify-between text-xs font-mono text-zinc-600 mb-2">
          <span>Checking addresses{provider ? ` (built-in + ${provider})` : ''}…</span>
          <span>{progress?.done ?? 0} / {progress?.total ?? '…'}</span>
        </div>
        <div className="h-1.5 bg-zinc-100 overflow-hidden">
          <div className="h-full bg-indigo-600 transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    )
  }
  if (!stats) return null

  return (
    <div className="mt-3 text-xs font-mono bg-zinc-50 px-4 py-2.5 border border-zinc-200 border-l-2 border-l-emerald-500">
      <div className="text-emerald-800">
        ✓ {stats.checked} checked · {stats.good} good · {stats.risky} risky (kept) · {stats.removed} removed
        {stats.duplicates ? ` · ${stats.duplicates} already contacted` : ''}
      </div>
      {risky.length > 0 && (
        <div className="mt-2">
          <button type="button" onClick={() => setShowRisky((s) => !s)} className="text-amber-800 hover:underline">
            {showRisky ? '▲' : '▼'} Kept but risky ({risky.length})
          </button>
          {showRisky && (
            <ul className="mt-1 divide-y divide-amber-100">
              {risky.map((entry) => (
                <Entry key={entry.email} entry={entry} onUseSuggestion={onUseSuggestion} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
