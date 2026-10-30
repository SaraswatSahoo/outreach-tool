import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { error: null }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error('UI error:', error, info)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="max-w-xl mx-auto mt-16 p-6 bg-red-50 border border-red-200 text-red-800">
        <h2 className="font-display font-bold text-lg mb-2">Something went wrong on this page</h2>
        <pre className="text-xs font-mono whitespace-pre-wrap mb-4">{String(this.state.error?.message || this.state.error)}</pre>
        <button
          onClick={() => { this.setState({ error: null }); window.location.assign('/') }}
          className="px-4 py-2 bg-red-700 text-white text-xs font-bold uppercase tracking-widest"
        >
          Back to Dashboard
        </button>
      </div>
    )
  }
}
