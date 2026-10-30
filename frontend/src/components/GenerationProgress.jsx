const TONES = {
  green: { bar: 'bg-green-500', track: 'bg-gray-200', running: 'text-blue-500' },
  amber: { bar: 'bg-amber-500', track: 'bg-amber-100', running: 'text-amber-600' },
}

const TITLES = {
  complete: '✅ Generation Complete',
  error: '❌ Generation Stopped',
  interrupted: '⚠️ Interrupted (server restarted)',
}

// Progress panel for parallel AI generation jobs (initial emails and follow-ups)
export default function GenerationProgress({ progress, runningLabel = '⚡ Generating Emails...', tone = 'green', children }) {
  const t = TONES[tone]
  const pct = progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0

  return (
    <div className="border border-gray-200 rounded-lg p-5 bg-white shadow-sm text-left">
      <div className="flex justify-between items-center mb-3">
        <span className="text-sm font-medium text-gray-700">{TITLES[progress.status] || runningLabel}</span>
        <span className="text-sm text-gray-500 font-mono">
          {progress.completed} / {progress.total}
        </span>
      </div>

      <div className={`w-full ${t.track} rounded-full h-2.5 mb-3 overflow-hidden`}>
        <div className={`${t.bar} h-2.5 rounded-full transition-all duration-500`} style={{ width: `${pct}%` }} />
      </div>

      <div className="flex gap-4 text-xs font-mono text-gray-500">
        <span>✅ {progress.completed} generated</span>
        {progress.failed > 0 && <span className="text-red-500">❌ {progress.failed} failed</span>}
        {progress.status === 'generating' && (
          <span className={`${t.running} animate-pulse ml-auto`}>Running in parallel...</span>
        )}
      </div>

      {progress.errors?.length > 0 && (
        <div className="mt-4 text-[10px] font-mono text-red-600 bg-red-50 border border-red-200 rounded p-3 max-h-32 overflow-y-auto">
          <div className="font-bold mb-1">Errors encountered:</div>
          {progress.errors.map((e, i) => (
            <div key={i} className="mb-1 truncate" title={e.error}>
              <span className="font-semibold">{e.email}:</span> {e.error}
            </div>
          ))}
        </div>
      )}

      {children}
    </div>
  )
}
