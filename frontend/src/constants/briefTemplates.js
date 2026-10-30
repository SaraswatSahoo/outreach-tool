// Starting points for the campaign brief builder. Each one pre-fills some answers;
// everything stays editable. Years are computed so the labels never go stale.

const now = new Date()
const year = now.getFullYear()
// After June, "summer" means next year's summer
export const NEXT_SUMMER = `Summer ${now.getMonth() >= 6 ? year + 1 : year}`
export const NEXT_WINTER = `Winter ${now.getMonth() >= 11 ? year + 1 : year}`

export const BRIEF_OPTIONS = {
  purpose: ['Internship', 'Full-time job', 'Research position', 'Referral', 'Informational chat', 'Freelance / project work'],
  timing: [NEXT_SUMMER, NEXT_WINTER, 'Starting now', 'After graduation', 'Flexible'],
  work_mode: ['Remote', 'On-site', 'Hybrid', 'Any'],
  audience: ['Startup founders', 'HR / recruiters', 'Engineers / team leads', 'Professors / researchers', 'Alumni'],
  tone: ['Friendly', 'Formal', 'Direct'],
  flexibility: ['Unpaid / any stipend is fine', 'Can relocate', 'Part-time is OK', 'Can start immediately'],
}

export const BRIEF_TEMPLATES = [
  {
    id: 'startup-internship',
    title: 'Summer internship at startups',
    blurb: 'Reach founders directly for a summer role.',
    answers: { purpose: 'Internship', timing: NEXT_SUMMER, work_mode: 'Any', audience: 'Startup founders', tone: 'Friendly' },
  },
  {
    id: 'research',
    title: 'Research internship with professors',
    blurb: 'Ask faculty for a research position.',
    answers: { purpose: 'Research position', timing: NEXT_SUMMER, audience: 'Professors / researchers', tone: 'Formal' },
  },
  {
    id: 'new-grad',
    title: 'New-grad full-time roles',
    blurb: 'Apply for a full-time job after graduating.',
    answers: { purpose: 'Full-time job', timing: 'After graduation', audience: 'HR / recruiters', tone: 'Direct' },
  },
  {
    id: 'referral',
    title: 'Referral from alumni',
    blurb: 'Ask alumni at a company to refer you.',
    answers: { purpose: 'Referral', audience: 'Alumni', tone: 'Friendly' },
  },
  {
    id: 'coffee-chat',
    title: 'Informational chat',
    blurb: 'Learn about a team or role — no direct ask.',
    answers: { purpose: 'Informational chat', audience: 'Engineers / team leads', tone: 'Friendly' },
  },
]
