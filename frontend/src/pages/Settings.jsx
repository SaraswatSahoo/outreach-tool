import { useState, useEffect, useCallback } from 'react'
import api from '../api'
import { useBlockedDomains } from '../context/BlockedDomainsContext'
import { errorMessage, useUI } from '../context/UIContext'
import { formatDate } from '../utils/dates'

const KEY_LABELS = {
  groq_api_key: 'Groq API Key',
  groq_api_key_2: 'Groq API Key 2',
  groq_api_key_3: 'Groq API Key 3',
}

const inputClass =
  'w-full border border-zinc-300 bg-zinc-50 rounded-none px-4 py-3 font-mono text-sm focus:bg-white focus:ring-1 focus:ring-zinc-900 focus:border-zinc-900 outline-none transition-colors'
const labelClass =
  'block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-2 flex items-center justify-between gap-2'

function ConfiguredBadge({ hint }) {
  return (
    <span className="text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 inline-block normal-case tracking-normal font-mono">
      ✓ configured {hint}
    </span>
  )
}

export default function Settings() {
  const [form, setForm] = useState({
    gmail_address: '',
    gmail_app_password: '',
    groq_api_key: '',
    groq_api_key_2: '',
    groq_api_key_3: '',
    send_delay_seconds: 60,
    ai_model: '',
    ai_fallback_model: '',
    ai_resume_model: '',
    verify_provider: '',
    verify_api_key: '',
    verify_with_service: true,
    send_window_enabled: false,
    send_window_start: 9,
    send_window_end: 18,
    send_window_weekdays_only: true,
    daily_send_cap: 0,
    warmup_enabled: false,
    warmup_start_per_day: 20,
    warmup_increase_per_day: 10,
    auto_resume_sends: true,
  })
  const [status, setStatus] = useState(null) // what the server says is configured
  const [toClear, setToClear] = useState([])
  const [saving, setSaving] = useState(false)
  const { toast, confirm } = useUI()

  const load = () =>
    api.get('/settings')
      .then((res) => {
        setStatus(res.data)
        setForm((prev) => ({
          ...prev,
          gmail_address: res.data.gmail_address,
          gmail_app_password: '',
          groq_api_key: '',
          groq_api_key_2: '',
          groq_api_key_3: '',
          send_delay_seconds: res.data.send_delay_seconds,
          send_window_enabled: !!res.data.send_window_enabled,
          send_window_start: res.data.send_window_start ?? 9,
          send_window_end: res.data.send_window_end ?? 18,
          send_window_weekdays_only: res.data.send_window_weekdays_only ?? true,
          daily_send_cap: res.data.daily_send_cap ?? 0,
          warmup_enabled: !!res.data.warmup_enabled,
          warmup_start_per_day: res.data.warmup_start_per_day ?? 20,
          warmup_increase_per_day: res.data.warmup_increase_per_day ?? 10,
          auto_resume_sends: res.data.auto_resume_sends ?? true,
          ai_model: res.data.ai_model || '',
          ai_fallback_model: res.data.ai_fallback_model || '',
          ai_resume_model: res.data.ai_resume_model || '',
          verify_provider: res.data.verify_provider || '',
          verify_api_key: '',
          verify_with_service: res.data.verify_with_service ?? true,
        }))
        setToClear([])
      })
      .catch((err) => toast.error(errorMessage(err, 'Failed to load settings')))

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load() }, [])

  const NUMERIC = ['send_delay_seconds', 'send_window_start', 'send_window_end', 'daily_send_cap',
    'warmup_start_per_day', 'warmup_increase_per_day']

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target
    setForm((prev) => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : NUMERIC.includes(name) ? parseInt(value, 10) || 0 : value,
    }))
  }

  const markClear = async (field) => {
    const ok = await confirm({
      title: `Remove ${KEY_LABELS[field]}?`,
      message: 'The key will be deleted when you save.',
      confirmLabel: 'Remove',
      danger: true,
    })
    if (ok) {
      setToClear((prev) => [...prev, field])
      setForm((prev) => ({ ...prev, [field]: '' }))
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    try {
      await api.post('/settings', { ...form, clear: toClear })
      toast.success('Settings saved')
      await load()
    } catch (err) {
      toast.error(errorMessage(err, 'Failed to save settings'))
    } finally {
      setSaving(false)
    }
  }

  const keyStatus = (field) => status?.groq_keys?.find((k) => k.field === field)
  const minDelay = status?.send_delay_min ?? 20
  const maxDelay = status?.send_delay_max ?? 90

  return (
    <div id="settings-page" className="max-w-2xl mx-auto pb-12">
      <div className="mb-8 pb-4 border-b border-zinc-200">
        <h1 className="text-3xl font-display font-bold text-zinc-950 tracking-tight">Settings</h1>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 bg-white p-6 sm:p-8 border border-zinc-200">
        {/* Gmail Address */}
        <div>
          <label htmlFor="gmail-address" className={labelClass}>Gmail Address</label>
          <input
            id="gmail-address"
            type="email"
            name="gmail_address"
            value={form.gmail_address}
            onChange={handleChange}
            placeholder="youremail@gmail.com"
            className={inputClass}
          />
        </div>

        {/* Gmail App Password */}
        <div>
          <label htmlFor="gmail-password" className={labelClass}>
            Gmail App Password
            {status?.has_gmail_password ? <ConfiguredBadge /> : null}
          </label>
          <input
            id="gmail-password"
            type="password"
            name="gmail_app_password"
            value={form.gmail_app_password}
            onChange={handleChange}
            autoComplete="new-password"
            placeholder={status?.has_gmail_password ? 'Leave blank to keep the current password' : 'Enter your app password'}
            className={inputClass}
          />
          <p className="mt-2 text-[11px] font-mono text-zinc-500 leading-relaxed">
            To generate a Gmail App Password, go to{' '}
            <a
              href="https://myaccount.google.com/security"
              target="_blank"
              rel="noopener noreferrer"
              className="text-indigo-600 hover:text-indigo-800 underline decoration-indigo-300 underline-offset-4"
            >
              Google Account → Security
            </a>{' '}
            → 2-Step Verification → App Passwords.
          </p>
        </div>

        {/* Groq API Keys */}
        {Object.keys(KEY_LABELS).map((field, i) => {
          const ks = keyStatus(field)
          const clearing = toClear.includes(field)
          return (
            <div key={field}>
              <label htmlFor={`groq-${field}`} className={labelClass}>
                <span>
                  {KEY_LABELS[field]}
                  {i > 0 && (
                    <span className="text-[10px] text-zinc-400 ml-2 font-normal lowercase tracking-normal">optional — for higher rate limits</span>
                  )}
                </span>
                {clearing ? (
                  <span className="text-red-700 normal-case tracking-normal font-mono">will be removed on save</span>
                ) : ks?.configured ? (
                  <span className="flex items-center gap-2">
                    <ConfiguredBadge hint={ks.hint} />
                    <button type="button" onClick={() => markClear(field)} className="text-red-600 hover:underline normal-case tracking-normal font-mono">
                      remove
                    </button>
                  </span>
                ) : null}
              </label>
              <input
                id={`groq-${field}`}
                type="password"
                name={field}
                value={form[field]}
                onChange={handleChange}
                autoComplete="off"
                placeholder={ks?.configured ? 'Leave blank to keep the current key' : 'gsk_...'}
                className={inputClass}
              />
            </div>
          )
        })}

        <div className="text-xs text-blue-600 bg-blue-50 border border-blue-200 rounded px-3 py-2 mt-1">
          💡 Adding several Groq keys spreads requests across them in rotation, which raises your effective rate limit.
          Get keys at <a href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer" className="underline">console.groq.com</a>.
          At least one key is required.
        </div>

        <AiModels form={form} onChange={handleChange} defaults={status?.default_models} hasKey={!!status?.groq_keys?.some((k) => k.configured)} />

        <EmailVerification
          form={form}
          onChange={handleChange}
          status={status}
          clearing={toClear.includes('verify_api_key')}
          onClearKey={() => {
            setToClear((prev) => [...prev, 'verify_api_key'])
            setForm((prev) => ({ ...prev, verify_api_key: '' }))
          }}
        />

        {/* Send Delay */}
        <div className="pb-6 border-b border-zinc-200">
          <label htmlFor="send-delay" className="block text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-4">
            Delay Between Emails: <span className="text-indigo-700 font-mono text-sm">{form.send_delay_seconds}s</span>
          </label>
          <input
            id="send-delay"
            type="range"
            name="send_delay_seconds"
            min={minDelay}
            max={maxDelay}
            value={form.send_delay_seconds}
            onChange={handleChange}
            className="w-full accent-indigo-700 h-2 bg-zinc-200 rounded-none appearance-none cursor-pointer"
          />
          <div className="flex justify-between text-[10px] font-mono text-zinc-400 uppercase tracking-widest mt-2">
            <span>{minDelay}s</span>
            <span>{maxDelay}s</span>
          </div>
        </div>

        {/* Sending schedule */}
        <div className="pb-6 border-b border-zinc-200 space-y-4">
          <div className={labelClass}>Sending Schedule</div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" name="send_window_enabled" checked={form.send_window_enabled} onChange={handleChange} className="accent-indigo-700" />
            Only send during set hours
          </label>
          {form.send_window_enabled && (
            <div className="pl-6 space-y-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                From
                <select name="send_window_start" value={form.send_window_start} onChange={handleChange} className="border border-zinc-300 px-2 py-1 font-mono text-sm">
                  {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                </select>
                to
                <select name="send_window_end" value={form.send_window_end} onChange={handleChange} className="border border-zinc-300 px-2 py-1 font-mono text-sm">
                  {Array.from({ length: 24 }, (_, i) => i + 1).map((h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
                </select>
                <span className="text-xs text-zinc-500">(this computer's time zone)</span>
              </div>
              <label className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" name="send_window_weekdays_only" checked={form.send_window_weekdays_only} onChange={handleChange} className="accent-indigo-700" />
                Weekdays only
              </label>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <label htmlFor="daily-cap">Daily limit</label>
            <input
              id="daily-cap"
              type="number"
              name="daily_send_cap"
              min={0}
              max={2000}
              value={form.daily_send_cap}
              onChange={handleChange}
              className="w-24 border border-zinc-300 px-2 py-1 font-mono text-sm"
            />
            <span className="text-xs text-zinc-500">emails per day, including follow-ups (0 = no limit; Gmail allows about 500)</span>
          </div>
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" name="warmup_enabled" checked={form.warmup_enabled} onChange={handleChange} className="accent-indigo-700 mt-1" />
            <span>
              Warm-up mode
              <span className="block text-xs text-zinc-500">
                For a new or rarely used Gmail account: start with a small daily limit and raise it every day.
              </span>
            </span>
          </label>
          {form.warmup_enabled && (
            <div className="pl-6 flex flex-wrap items-center gap-2 text-sm">
              Start at
              <input type="number" name="warmup_start_per_day" min={1} max={500} value={form.warmup_start_per_day}
                onChange={handleChange} className="w-20 border border-zinc-300 px-2 py-1 font-mono text-sm" />
              per day, then add
              <input type="number" name="warmup_increase_per_day" min={0} max={200} value={form.warmup_increase_per_day}
                onChange={handleChange} className="w-20 border border-zinc-300 px-2 py-1 font-mono text-sm" />
              each day.
              {status?.warmup_enabled && status?.warmup_cap_today != null && (
                <span className="text-xs font-mono text-indigo-700">Today's limit: {status.warmup_cap_today}</span>
              )}
            </div>
          )}
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <input type="checkbox" name="auto_resume_sends" checked={form.auto_resume_sends} onChange={handleChange} className="accent-indigo-700 mt-1" />
            <span>
              Resume interrupted sends
              <span className="block text-xs text-zinc-500">
                If the backend restarts mid-send, continue with the remaining drafts automatically (within 24 hours).
              </span>
            </span>
          </label>
          <p className="text-[11px] font-mono text-zinc-500">
            Sends that hit these limits pause and continue automatically — keep the backend running.
          </p>
        </div>

        <div className="pt-2">
          <button
            id="save-settings-btn"
            type="submit"
            disabled={saving}
            className="w-full py-4 bg-indigo-700 text-white font-display font-bold rounded-none hover:bg-indigo-800 disabled:opacity-50 transition-colors uppercase tracking-widest text-xs"
          >
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </form>

      <BlockedDomainsSection />
    </div>
  )
}

const MODEL_FIELDS = [
  { name: 'ai_model', label: 'Main model', help: 'Writes emails and follow-ups, classifies replies.' },
  { name: 'ai_fallback_model', label: 'Fallback model', help: 'Used when the main model is rate-limited or retired.' },
  { name: 'ai_resume_model', label: 'Resume parsing model', help: 'Reads your resume once per upload; accuracy matters most.' },
]

// Groq retires models regularly; let the user pick from what their key can use today
// Optional mailbox-checking service for the email list (free tiers; user's own key)
function EmailVerification({ form, onChange, status, clearing, onClearKey }) {
  const [credits, setCredits] = useState(null)
  const providers = status?.verify_providers || []
  const chosen = providers.find((p) => p.name === form.verify_provider)
  const savedProvider = status?.verify_provider
  const hasKey = !!status?.has_verify_key

  useEffect(() => {
    if (!savedProvider || !hasKey) {
      setCredits(null)
      return
    }
    api.get('/emails/credits').then((res) => setCredits(res.data)).catch(() => setCredits(null))
  }, [savedProvider, hasKey])

  return (
    <div className="pb-6 border-b border-zinc-200 space-y-4">
      <div className={labelClass}>Email verification</div>
      <p className="text-[11px] font-mono text-zinc-500 -mt-2">
        Every email list is checked for typos, dead domains, disposable and previously bounced addresses — free and
        automatic. Optionally, add a free account with one of these services to also check that each mailbox exists.
      </p>
      <div>
        <label htmlFor="verify-provider" className="block text-xs text-zinc-700 mb-1">Mailbox check service</label>
        <select id="verify-provider" name="verify_provider" value={form.verify_provider} onChange={onChange} className={inputClass}>
          <option value="">None — built-in checks only</option>
          {providers.map((p) => <option key={p.name} value={p.name}>{p.label}</option>)}
        </select>
        {chosen && (
          <p className="text-[11px] text-zinc-500 mt-1">
            Free plan sign-up:{' '}
            <a href={chosen.signup} target="_blank" rel="noopener noreferrer" className="text-indigo-700 underline">{chosen.signup}</a>
          </p>
        )}
      </div>
      {form.verify_provider && (
        <>
          <div>
            <label htmlFor="verify-key" className="flex items-center justify-between text-xs text-zinc-700 mb-1">
              <span>{chosen?.label || 'Service'} API key</span>
              {clearing ? (
                <span className="text-red-700 font-mono">will be removed on save</span>
              ) : hasKey ? (
                <span className="flex items-center gap-2">
                  <ConfiguredBadge hint={status?.verify_key_hint} />
                  <button type="button" onClick={onClearKey} className="text-red-600 hover:underline font-mono">remove</button>
                </span>
              ) : null}
            </label>
            <input
              id="verify-key"
              type="password"
              name="verify_api_key"
              value={form.verify_api_key}
              onChange={onChange}
              autoComplete="off"
              placeholder={hasKey ? 'Leave blank to keep the current key' : 'Paste your API key'}
              className={inputClass}
            />
          </div>
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" name="verify_with_service" checked={form.verify_with_service} onChange={onChange} className="accent-indigo-700" />
            Check mailboxes with this service when I paste a list
          </label>
          {credits && (credits.credits != null || credits.error) && (
            <p className={`text-xs font-mono ${credits.error ? 'text-red-600' : 'text-zinc-600'}`}>
              {credits.error ? `${credits.provider}: ${credits.error}` : `${credits.provider}: ${credits.credits} checks left`}
            </p>
          )}
          <p className="text-[11px] text-zinc-500 bg-amber-50 border border-amber-200 px-3 py-2">
            Your contacts' email addresses are sent to {chosen?.label || 'this service'} for checking. Only addresses that
            pass the free checks are sent, and each result is remembered for 30 days so credits aren't used twice.
            The check runs from the service's servers, so it can't affect your Gmail or IP reputation.
          </p>
        </>
      )}
    </div>
  )
}

function AiModels({ form, onChange, defaults, hasKey }) {
  const [available, setAvailable] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const fetchModels = useCallback(() => {
    setLoading(true)
    setError('')
    api.get('/ai/models')
      .then((res) => setAvailable(res.data.models))
      .catch((err) => setError(errorMessage(err, 'Could not load models')))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (hasKey) fetchModels()
  }, [hasKey, fetchModels])

  const unknown = (value) => value && available && !available.includes(value)

  return (
    <div className="pb-6 border-b border-zinc-200 space-y-4">
      <div className={labelClass}>
        <span>AI Models</span>
        {hasKey && (
          <button type="button" onClick={fetchModels} disabled={loading}
            className="text-indigo-700 hover:underline normal-case tracking-normal font-mono disabled:opacity-50">
            {loading ? 'checking…' : 'refresh list'}
          </button>
        )}
      </div>
      <p className="text-[11px] font-mono text-zinc-500 -mt-2">
        Leave blank to use the recommended defaults. If Groq retires a model, the app moves on to the next one
        automatically — pick a replacement here if that happens.
      </p>
      <datalist id="groq-models">
        {(available || []).map((m) => <option key={m} value={m} />)}
      </datalist>
      {MODEL_FIELDS.map((f) => (
        <div key={f.name}>
          <label htmlFor={f.name} className="block text-xs text-zinc-700 mb-1">{f.label}</label>
          <input
            id={f.name}
            name={f.name}
            list="groq-models"
            value={form[f.name]}
            onChange={onChange}
            placeholder={defaults?.[f.name] ? `${defaults[f.name]} (default)` : ''}
            className={inputClass}
            autoComplete="off"
          />
          <p className="text-[11px] text-zinc-500 mt-1">
            {f.help}
            {unknown(form[f.name]) && (
              <span className="text-red-600 ml-1">Not in your account's model list — it may be retired.</span>
            )}
          </p>
        </div>
      ))}
      {available && (
        <p className="text-[11px] font-mono text-zinc-500">Available to your key: {available.join(', ') || 'none'}</p>
      )}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  )
}

function BlockedDomainsSection() {
  const { entries, block, unblock } = useBlockedDomains()
  const [domain, setDomain] = useState('')

  const handleAdd = async (e) => {
    e.preventDefault()
    const d = domain.trim().toLowerCase().replace(/^@/, '')
    if (!d) return
    if (await block(d)) setDomain('')
  }

  return (
    <div className="mt-10 bg-white p-6 sm:p-8 border border-zinc-200">
      <h2 className="text-[10px] font-display font-bold text-zinc-950 uppercase tracking-widest mb-1">Blocked Domains</h2>
      <p className="text-[11px] font-mono text-zinc-500 mb-4">
        Nobody at these domains will be emailed. They're also skipped when you create a campaign.
      </p>

      <form onSubmit={handleAdd} className="flex gap-2 mb-4">
        <input
          type="text"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          placeholder="example.com"
          className={inputClass}
        />
        <button type="submit" className="px-5 bg-zinc-900 text-white text-[10px] font-bold uppercase tracking-widest hover:bg-zinc-700">
          Block
        </button>
      </form>

      {entries.length === 0 ? (
        <p className="text-xs font-mono text-zinc-400">No blocked domains.</p>
      ) : (
        <ul className="divide-y divide-zinc-100 border border-zinc-200">
          {entries.map((e) => (
            <li key={e.domain} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
              <div className="min-w-0">
                <span className="font-mono text-zinc-900">{e.domain}</span>
                {e.reason ? <span className="text-xs text-zinc-500 ml-2">— {e.reason}</span> : null}
                <div className="text-[10px] font-mono text-zinc-400">blocked {formatDate(e.blocked_at)}</div>
              </div>
              <button onClick={() => unblock(e.domain)} className="text-xs text-red-600 hover:underline shrink-0">
                Unblock
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
