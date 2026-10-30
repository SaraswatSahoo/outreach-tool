import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import api from '../api'
import { errorMessage, useUI } from './UIContext'

// Single source of truth for blocked domains, so blocking in one place
// (search results, contact drawer, settings) updates every other view.

const BlockedDomainsContext = createContext(null)

export const domainOf = (email) => email?.split('@')[1]?.toLowerCase() || ''

export function BlockedDomainsProvider({ children }) {
  const [entries, setEntries] = useState([])
  const { toast, prompt } = useUI()

  const refresh = useCallback(() => {
    return api.get('/blocked-domains')
      .then((res) => setEntries(res.data))
      .catch(() => {})
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const domains = useMemo(() => entries.map((e) => e.domain), [entries])

  const isBlocked = useCallback((emailOrDomain) => {
    const d = emailOrDomain?.includes('@') ? domainOf(emailOrDomain) : emailOrDomain?.toLowerCase()
    return !!d && domains.includes(d)
  }, [domains])

  const block = useCallback(async (domain, { askReason = true } = {}) => {
    let reason = ''
    if (askReason) {
      reason = await prompt({
        title: `Block ${domain}?`,
        message: 'No emails or follow-ups will be sent to anyone at this domain.',
        placeholder: 'Reason (optional)',
        confirmLabel: 'Block',
      })
      if (reason === null) return false
    }
    try {
      await api.post('/blocked-domains', { domain, reason })
      await refresh()
      toast.success(`${domain} blocked`)
      return true
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to block domain'))
      return false
    }
  }, [prompt, refresh, toast])

  const unblock = useCallback(async (domain) => {
    try {
      await api.delete(`/blocked-domains/${encodeURIComponent(domain)}`)
      await refresh()
      toast.success(`${domain} unblocked`)
      return true
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to unblock domain'))
      return false
    }
  }, [refresh, toast])

  const toggle = useCallback(
    (domain) => (domains.includes(domain) ? unblock(domain) : block(domain)),
    [domains, block, unblock],
  )

  const value = useMemo(
    () => ({ entries, domains, isBlocked, block, unblock, toggle, refresh }),
    [entries, domains, isBlocked, block, unblock, toggle, refresh],
  )

  return <BlockedDomainsContext.Provider value={value}>{children}</BlockedDomainsContext.Provider>
}

export function useBlockedDomains() {
  const ctx = useContext(BlockedDomainsContext)
  if (!ctx) throw new Error('useBlockedDomains must be used inside <BlockedDomainsProvider>')
  return ctx
}
