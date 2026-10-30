import { Link } from 'react-router-dom'

export default function NotFound() {
  return (
    <div className="text-center py-24">
      <h1 className="text-3xl font-display font-bold text-zinc-950 mb-3">Page not found</h1>
      <Link to="/" className="text-indigo-600 hover:underline text-sm font-medium">Back to Dashboard →</Link>
    </div>
  )
}
