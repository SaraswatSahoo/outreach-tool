// Reply statuses. Keep in sync with backend/constants.py.
export const REPLY_STATUSES = [
  { value: 'no_reply', label: 'No Reply', dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-800' },
  { value: 'check_back', label: 'Check Back', dot: 'bg-blue-500', badge: 'bg-blue-100 text-blue-800' },
  { value: 'interested', label: 'Interested', dot: 'bg-green-500', badge: 'bg-green-100 text-green-800' },
  { value: 'no_openings', label: 'No Openings', dot: 'bg-orange-400', badge: 'bg-amber-100 text-amber-800' },
  { value: 'interview_scheduled', label: 'Interview Scheduled', dot: 'bg-purple-500', badge: 'bg-purple-100 text-purple-800' },
  { value: 'final_rejection', label: 'Rejected', dot: 'bg-red-500', badge: 'bg-red-100 text-red-800' },
  { value: 'invalid_email', label: 'Invalid Email', dot: 'bg-red-900', badge: 'bg-red-500 text-white' },
]

// Send statuses of the original email
export const SEND_STATUSES = [
  { value: 'draft', label: 'Draft', badge: 'bg-gray-100 text-gray-800' },
  { value: 'sent', label: 'Sent', badge: 'bg-gray-100 text-gray-800' },
  { value: 'failed', label: 'Failed', badge: 'bg-red-100 text-red-800' },
]

// Statuses that permanently stop follow-ups
export const AUTO_EXCLUDED_STATUSES = ['invalid_email', 'interview_scheduled', 'final_rejection']

// Not eligible for a batch follow-up (interested contacts get a personal reply instead)
export const FOLLOWUP_INELIGIBLE_STATUSES = [...AUTO_EXCLUDED_STATUSES, 'interested']

const BY_VALUE = Object.fromEntries([...SEND_STATUSES, ...REPLY_STATUSES].map((s) => [s.value, s]))

export function statusInfo(value) {
  return BY_VALUE[value] || { value, label: value || '—', dot: 'bg-gray-400', badge: 'bg-gray-100 text-gray-800' }
}

// The status to show for a contact: reply status once sent, otherwise the send status
export function displayStatus(record) {
  const sendStatus = record.send_status || record.status
  return sendStatus === 'sent' ? record.reply_status || 'no_reply' : sendStatus
}
