import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

export default function Settings() {
  const [recipients, setRecipients] = useState('')
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then(data => {
        setRecipients(data.email_recipients || '')
        setLoading(false)
      })
  }, [])

  const handleSave = () => {
    fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email_recipients: recipients })
    }).then(() => {
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    })
  }

  if (loading) return <div className="p-8 text-center">Loading...</div>

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
          <Link to="/" className="text-blue-600 hover:text-blue-800 text-sm">&larr; Dashboard</Link>
          <h1 className="text-xl font-bold text-gray-800">Settings</h1>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6">
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-4">Email Notifications</h2>
          <p className="text-sm text-gray-500 mb-4">
            Configure who receives inspection alert emails. Separate multiple addresses with commas.
            Recipients must be verified Snowflake users in this account.
          </p>

          <label className="block text-sm font-medium text-gray-700 mb-1">
            Email Recipients
          </label>
          <input
            type="text"
            value={recipients}
            onChange={e => setRecipients(e.target.value)}
            placeholder="user@company.com, user2@company.com"
            className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 text-sm"
          />

          <div className="mt-4 flex items-center gap-3">
            <button
              onClick={handleSave}
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              Save Settings
            </button>
            {saved && (
              <span className="text-green-600 text-sm font-medium">Saved successfully</span>
            )}
          </div>
        </div>
      </main>
    </div>
  )
}
