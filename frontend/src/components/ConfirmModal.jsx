import { useEffect, useRef, useState } from 'react'

// Confirmation dialog. Pass `input` ({ placeholder }) to also collect a line of text;
// onConfirm then receives the entered value.
export default function ConfirmModal({
  isOpen, title, message, onConfirm, onCancel,
  confirmLabel = 'Confirm', danger = false, input = null,
}) {
  const [value, setValue] = useState('')
  const inputRef = useRef(null)
  const confirmRef = useRef(null)

  useEffect(() => {
    if (!isOpen) return
    setValue('')
    // Focus the input (or the confirm button) so Enter/Escape work immediately
    setTimeout(() => (inputRef.current || confirmRef.current)?.focus(), 0)
    const onKey = (e) => { if (e.key === 'Escape') onCancel() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [isOpen, onCancel])

  if (!isOpen) return null

  const confirm = () => onConfirm(input ? value : undefined)

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center">
      <div
        className="fixed inset-0 bg-zinc-900/60 backdrop-blur-sm"
        onClick={onCancel}
        aria-hidden="true"
      />
      <div
        id="confirm-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-modal-title"
        className="relative bg-white border border-zinc-200 rounded-none max-w-md w-full mx-4 p-8"
      >
        <h3 id="confirm-modal-title" className="text-xl font-display font-bold text-zinc-950 tracking-tight mb-3">{title}</h3>
        <p className="text-sm font-mono text-zinc-600 mb-6 leading-relaxed whitespace-pre-line">{message}</p>
        {input ? (
          <input
            ref={inputRef}
            type="text"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') confirm() }}
            placeholder={input.placeholder}
            className="w-full border border-zinc-300 rounded-none px-3 py-2 text-sm font-mono mb-6 focus:ring-1 focus:ring-zinc-900 outline-none"
          />
        ) : null}
        <div className="flex justify-end gap-3 pt-4 border-t border-zinc-100">
          <button
            id="confirm-modal-cancel"
            onClick={onCancel}
            className="px-6 py-2.5 text-[10px] font-mono uppercase tracking-widest text-zinc-700 bg-zinc-50 border border-zinc-200 rounded-none hover:bg-zinc-100 hover:text-zinc-900 transition-colors"
          >
            Cancel
          </button>
          <button
            id="confirm-modal-confirm"
            ref={confirmRef}
            onClick={confirm}
            className={`px-6 py-2.5 text-[10px] font-mono uppercase tracking-widest text-white rounded-none transition-colors ${
              danger ? 'bg-red-700 hover:bg-red-800' : 'bg-indigo-700 hover:bg-indigo-800'
            }`}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
