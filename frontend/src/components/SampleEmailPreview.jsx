import { useState } from 'react'
import api from '../api'
import { errorMessage, useUI } from '../context/UIContext'

// Write one email from the current brief before generating the whole batch.
// Nothing is saved or sent.
export default function SampleEmailPreview({ goal, additionalContext, recipient }) {
  const [sample, setSample] = useState(null)
  const [loading, setLoading] = useState(false)
  const { toast } = useUI()

  const generate = async () => {
    setLoading(true)
    try {
      const { data } = await api.post('/campaign/sample-email', {
        goal,
        additional_context: additionalContext,
        recipient,
      })
      setSample(data)
    } catch (err) {
      toast.error(errorMessage(err, 'Couldn\'t write a sample email'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={generate}
          disabled={!goal.trim() || loading}
          className="px-4 py-2 border border-indigo-300 bg-white text-indigo-800 text-[11px] font-bold uppercase tracking-widest hover:bg-indigo-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {loading ? 'Writing…' : sample ? 'Try again' : 'Preview a sample email'}
        </button>
        <span className="text-[11px] font-mono text-zinc-500">
          {goal.trim()
            ? `One email to ${recipient?.email || 'an example contact'} — nothing is saved or sent.`
            : 'Write the campaign goal first.'}
        </span>
      </div>

      {sample && (
        <div className="mt-3 border border-zinc-200 bg-white">
          <div className="flex justify-between items-center px-4 py-2 border-b border-zinc-100 bg-zinc-50">
            <span className="text-xs font-mono text-zinc-600 truncate">To: {sample.email}</span>
            <button type="button" onClick={() => setSample(null)} className="text-xs text-zinc-500 hover:underline">Close</button>
          </div>
          <div className="px-4 py-3">
            <div className="text-sm font-medium text-zinc-900 mb-2">{sample.subject}</div>
            <pre className="text-sm text-zinc-700 whitespace-pre-wrap font-sans leading-relaxed">{sample.body}</pre>
          </div>
          <p className="px-4 pb-3 text-[11px] text-zinc-500">
            Not quite right? Adjust the goal or details above and try again. Every recipient gets their own version.
          </p>
        </div>
      )}
    </div>
  )
}
