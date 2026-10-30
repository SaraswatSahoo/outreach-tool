import { statusInfo } from '../constants/status'

export default function StatusBadge({ status }) {
  const { label, badge } = statusInfo(status)
  return <span className={`px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${badge}`}>{label}</span>
}
