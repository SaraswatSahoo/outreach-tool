import { useEffect, useState } from 'react'
import api from '../api'
import { BRIEF_OPTIONS, BRIEF_TEMPLATES } from '../constants/briefTemplates'
import { errorMessage, useUI } from '../context/UIContext'

// "Help me write this": pick a template or answer a few quick questions, and the
// AI writes the campaign goal + additional context from them and your profile.
// Answers are remembered between campaigns (this browser only).

const STORAGE_KEY = 'outreach.briefAnswers'
const EMPTY = {
  purpose: '', field: '', timing: '', dates: '', work_mode: '', location: '', audience: '',
  project_to_highlight: '', availability: '', flexibility: [], links: [], tone: '', anything_else: '',
}

function loadSaved() {
  try {
    return { ...EMPTY, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}') }
  } catch {
    return { ...EMPTY }
  }
}

function Chips({ options, value, onChange, multi = false }) {
  const selected = multi ? value : [value]
  const toggle = (option) => {
    if (multi) onChange(selected.includes(option) ? selected.filter((v) => v !== option) : [...selected, option])
    else onChange(value === option ? '' : option)
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          onClick={() => toggle(option)}
          aria-pressed={selected.includes(option)}
          className={`px-2.5 py-1 text-xs border transition-colors ${
            selected.includes(option)
              ? 'bg-indigo-700 border-indigo-700 text-white'
              : 'bg-white border-zinc-300 text-zinc-700 hover:border-zinc-500'
          }`}
        >
          {option}
        </button>
      ))}
    </div>
  )
}

function Row({ label, hint, children }) {
  return (
    <div>
      <div className="text-xs font-medium text-zinc-800 mb-1.5">
        {label} {hint && <span className="text-zinc-400 font-normal">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

const inputClass = 'w-full border border-zinc-300 bg-white px-3 py-1.5 text-sm focus:ring-1 focus:ring-zinc-900 outline-none'

export default function BriefBuilder({ hasGoal, onApply }) {
  const [open, setOpen] = useState(!hasGoal)
  const [answers, setAnswers] = useState(loadSaved)
  const [profile, setProfile] = useState({ projects: [], skills: [], links: [] })
  const [writing, setWriting] = useState(false)
  const { toast, confirm } = useUI()

  useEffect(() => {
    api.get('/profile')
      .then(({ data }) => {
        const parsed = data.resume_parsed || {}
        const links = [
          data.profile?.github && { label: 'GitHub', value: data.profile.github },
          data.profile?.linkedin && { label: 'LinkedIn', value: data.profile.linkedin },
        ].filter(Boolean)
        const skills = (parsed.skills?.length ? parsed.skills : (data.profile?.skills || '').split(','))
          .map((s) => s.trim()).filter(Boolean).slice(0, 8)
        setProfile({ projects: (parsed.projects || []).map((p) => p.title).filter(Boolean), skills, links })
      })
      .catch(() => {})
  }, [])

  const set = (key) => (value) => setAnswers((prev) => ({ ...prev, [key]: value }))
  const setFromEvent = (key) => (e) => set(key)(e.target.value)

  const applyTemplate = (template) => setAnswers((prev) => ({ ...prev, ...template.answers }))

  const write = async () => {
    if (!answers.purpose && !answers.field) {
      toast.error('Pick what you\'re looking for, or type a field/role')
      return
    }
    if (hasGoal) {
      const ok = await confirm({
        title: 'Replace what you wrote?',
        message: 'The campaign goal and additional context will be replaced with the new version.',
        confirmLabel: 'Replace',
      })
      if (!ok) return
    }
    setWriting(true)
    try {
      const payload = {
        ...answers,
        flexibility: answers.flexibility.join('; '),
        links: profile.links.filter((l) => answers.links.includes(l.label)).map((l) => `${l.label}: ${l.value}`).join('; '),
      }
      const { data } = await api.post('/campaign/brief', payload)
      onApply(data)
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(answers)) } catch { /* private mode */ }
      toast.success('Done — review and edit the text below')
    } catch (err) {
      toast.error(errorMessage(err, 'Couldn\'t write the brief'))
    } finally {
      setWriting(false)
    }
  }

  return (
    <div className="border border-indigo-200 bg-indigo-50/40">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between px-4 py-3 text-left"
        aria-expanded={open}
      >
        <span className="font-display font-semibold text-indigo-900">✨ Help me write this</span>
        <span className="text-xs text-indigo-700">{open ? 'Hide' : 'Show'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-5">
          <div>
            <div className="text-xs font-medium text-zinc-800 mb-2">Start from a template</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
              {BRIEF_TEMPLATES.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => applyTemplate(t)}
                  className="text-left border border-zinc-200 bg-white px-3 py-2 hover:border-indigo-400 transition-colors"
                >
                  <div className="text-sm font-medium text-zinc-900">{t.title}</div>
                  <div className="text-xs text-zinc-500">{t.blurb}</div>
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4 border-t border-indigo-100 pt-4">
            <Row label="What are you looking for?">
              <Chips options={BRIEF_OPTIONS.purpose} value={answers.purpose} onChange={set('purpose')} />
            </Row>
            <Row label="Which field or role?">
              <input value={answers.field} onChange={setFromEvent('field')} placeholder="e.g., Machine learning, backend engineering" className={inputClass} />
              {profile.skills.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {profile.skills.map((skill) => (
                    <button key={skill} type="button" onClick={() => set('field')(answers.field ? `${answers.field}, ${skill}` : skill)}
                      className="text-[11px] px-1.5 py-0.5 bg-white border border-zinc-200 text-zinc-600 hover:border-zinc-400">
                      + {skill}
                    </button>
                  ))}
                </div>
              )}
            </Row>
            <Row label="When?">
              <Chips options={BRIEF_OPTIONS.timing} value={answers.timing} onChange={set('timing')} />
              <input value={answers.dates} onChange={setFromEvent('dates')} placeholder="Exact dates (optional), e.g., May 15 – July 31" className={`${inputClass} mt-2`} />
            </Row>
            <Row label="Where?">
              <Chips options={BRIEF_OPTIONS.work_mode} value={answers.work_mode} onChange={set('work_mode')} />
              <input value={answers.location} onChange={setFromEvent('location')} placeholder="City (optional)" className={`${inputClass} mt-2`} />
            </Row>
            <Row label="Who are you emailing?">
              <Chips options={BRIEF_OPTIONS.audience} value={answers.audience} onChange={set('audience')} />
            </Row>
          </div>

          <div className="space-y-4 border-t border-indigo-100 pt-4">
            <div className="text-xs text-zinc-500">Optional details — the AI only mentions what you give here, never invents it.</div>
            <Row label="Project to highlight">
              <select value={answers.project_to_highlight} onChange={setFromEvent('project_to_highlight')} className={inputClass}>
                <option value="">Let the AI pick the best match for each company</option>
                {profile.projects.map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
              {profile.projects.length === 0 && (
                <p className="text-[11px] text-zinc-400 mt-1">Upload and parse your resume in Profile to choose a project here.</p>
              )}
            </Row>
            <Row label="Availability" hint="(start date, hours per week, duration)">
              <input value={answers.availability} onChange={setFromEvent('availability')} placeholder="e.g., Full-time from June 1 for 10 weeks" className={inputClass} />
            </Row>
            <Row label="Flexibility">
              <Chips options={BRIEF_OPTIONS.flexibility} value={answers.flexibility} onChange={set('flexibility')} multi />
            </Row>
            {profile.links.length > 0 && (
              <Row label="Links to include">
                <Chips options={profile.links.map((l) => l.label)} value={answers.links} onChange={set('links')} multi />
              </Row>
            )}
            <Row label="Tone">
              <Chips options={BRIEF_OPTIONS.tone} value={answers.tone} onChange={set('tone')} />
            </Row>
            <Row label="Anything else?">
              <textarea value={answers.anything_else} onChange={setFromEvent('anything_else')} rows={2}
                placeholder="e.g., I met some of them at PyCon; mention my Kaggle top-5% finish" className={inputClass} />
            </Row>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={write}
              disabled={writing}
              className="px-5 py-2.5 bg-indigo-700 text-white text-xs font-bold uppercase tracking-widest hover:bg-indigo-800 disabled:opacity-50"
            >
              {writing ? 'Writing…' : 'Write it for me'}
            </button>
            <button type="button" onClick={() => setAnswers({ ...EMPTY })} className="text-xs text-zinc-500 hover:underline">
              Clear answers
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
