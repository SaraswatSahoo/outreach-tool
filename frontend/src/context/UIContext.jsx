import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import ConfirmModal from '../components/ConfirmModal'

// App-wide toasts and promise-based dialogs, replacing window.alert/confirm/prompt.
//   const { toast, confirm, prompt } = useUI()
//   toast.error('Failed'); if (await confirm({ title, message, danger: true })) ...
//   const reason = await prompt({ title, message })   // null when cancelled

const UIContext = createContext(null)

const TOAST_STYLES = {
  success: 'bg-emerald-50 border-emerald-300 text-emerald-800',
  error: 'bg-red-50 border-red-300 text-red-800',
  info: 'bg-white border-zinc-300 text-zinc-800',
}

export function UIProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const [dialog, setDialog] = useState(null)
  const nextId = useRef(0)

  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), [])

  const push = useCallback((kind, message) => {
    const id = ++nextId.current
    setToasts((t) => [...t, { id, kind, message }])
    setTimeout(() => dismiss(id), kind === 'error' ? 6000 : 3000)
  }, [dismiss])

  const toast = useMemo(() => ({
    success: (m) => push('success', m),
    error: (m) => push('error', m),
    info: (m) => push('info', m),
  }), [push])

  const dialogRef = useRef(null)

  const openDialog = useCallback((options, withInput) => new Promise((resolve) => {
    const prev = dialogRef.current // never leave an earlier caller hanging
    prev?.resolve(prev.input ? null : false)
    const d = { ...options, input: withInput ? { placeholder: options.placeholder || '' } : null, resolve }
    dialogRef.current = d
    setDialog(d)
  }), [])

  const confirm = useCallback((options) => openDialog(options, false), [openDialog])
  const prompt = useCallback((options) => openDialog(options, true), [openDialog])

  const close = useCallback((result) => {
    const d = dialogRef.current
    dialogRef.current = null
    setDialog(null)
    d?.resolve(result)
  }, [])

  const handleCancel = useCallback(() => close(dialog?.input ? null : false), [close, dialog])

  const value = useMemo(() => ({ toast, confirm, prompt }), [toast, confirm, prompt])

  return (
    <UIContext.Provider value={value}>
      {children}

      <ConfirmModal
        isOpen={!!dialog}
        title={dialog?.title}
        message={dialog?.message}
        confirmLabel={dialog?.confirmLabel}
        danger={dialog?.danger}
        input={dialog?.input}
        onConfirm={(inputValue) => close(dialog?.input ? inputValue : true)}
        onCancel={handleCancel}
      />

      <div className="fixed bottom-4 right-4 z-[70] flex flex-col gap-2 max-w-sm w-[calc(100%-2rem)]" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === 'error' ? 'alert' : 'status'}
            className={`flash-message border px-4 py-3 text-sm font-mono shadow-sm flex justify-between gap-3 ${TOAST_STYLES[t.kind]}`}
          >
            <span className="break-words">{t.message}</span>
            <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100" aria-label="Dismiss">✕</button>
          </div>
        ))}
      </div>
    </UIContext.Provider>
  )
}

export function useUI() {
  const ctx = useContext(UIContext)
  if (!ctx) throw new Error('useUI must be used inside <UIProvider>')
  return ctx
}

// Pull a readable message out of an axios error
export function errorMessage(err, fallback = 'Something went wrong') {
  return err?.response?.data?.error || err?.message || fallback
}
