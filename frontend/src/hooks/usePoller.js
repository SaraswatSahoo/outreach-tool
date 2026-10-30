import { useCallback, useEffect, useMemo, useRef } from 'react'

// Imperative polling that is always cleaned up on unmount.
//   const poller = usePoller()
//   poller.start(async () => { ...; return done }, 1500)
// The callback returns true to stop. A thrown error stops polling and is
// passed to onError.
export default function usePoller() {
  const timer = useRef(null)

  const stop = useCallback(() => {
    clearTimeout(timer.current)
    timer.current = null
  }, [])

  const start = useCallback((tick, intervalMs, onError) => {
    stop()
    const run = async () => {
      try {
        const done = await tick()
        if (!done) timer.current = setTimeout(run, intervalMs)
        else timer.current = null
      } catch (err) {
        timer.current = null
        if (onError) onError(err)
      }
    }
    timer.current = setTimeout(run, 0)
  }, [stop])

  useEffect(() => stop, [stop])

  return useMemo(() => ({ start, stop }), [start, stop])
}
