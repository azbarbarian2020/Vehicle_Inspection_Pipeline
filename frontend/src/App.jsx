import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'

export default function App() {
  const [inspections, setInspections] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.all([
      fetch('/api/inspections').then(r => r.json()),
      fetch('/api/stats').then(r => r.json())
    ]).then(([data, statsData]) => {
      setInspections(data.inspections || [])
      setStats(statsData)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  if (loading) return <div className="p-8 text-center">Loading...</div>

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4">
          <h1 className="text-2xl font-bold text-gray-800">Vehicle Inspection Dashboard</h1>
          <p className="text-sm text-gray-500 mt-1">Automated inspection report processing</p>
        </div>
      </header>

      {stats && (
        <div className="max-w-7xl mx-auto px-6 py-4">
          <div className="grid grid-cols-4 gap-4">
            <StatCard label="Inspections" value={stats.TOTAL_INSPECTIONS} />
            <StatCard label="Total Failures" value={stats.TOTAL_FAILURES} color="red" />
            <StatCard label="Images Captured" value={stats.TOTAL_IMAGES} />
            <StatCard label="Pending Files" value={stats.PENDING_FILES} color={stats.PENDING_FILES > 0 ? 'yellow' : 'green'} />
          </div>
        </div>
      )}

      <main className="max-w-7xl mx-auto px-6 py-4">
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Inspection #</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Company</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Unit #</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Inspector</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Complete Date</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Status</th>
                <th className="px-4 py-3 text-center font-semibold text-gray-600">Failures</th>
                <th className="px-4 py-3 text-center font-semibold text-gray-600">Images</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {inspections.map(insp => (
                <tr key={insp.INSPECTION_ID} className="hover:bg-blue-50 cursor-pointer">
                  <td className="px-4 py-3">
                    <Link to={`/inspection/${insp.INSPECTION_ID}`} className="text-blue-600 font-medium hover:underline">
                      #{insp.INSPECTION_NUM}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-700">{insp.COMPANY}</td>
                  <td className="px-4 py-3 text-gray-700">{insp.UNIT_NUM}</td>
                  <td className="px-4 py-3 text-gray-700">{insp.INSPECTOR}</td>
                  <td className="px-4 py-3 text-gray-700">{insp.COMPLETE_DATE}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-1 rounded text-xs font-medium ${
                      insp.STATUS === 'Completed' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
                    }`}>
                      {insp.STATUS}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="bg-red-100 text-red-700 px-2 py-1 rounded text-xs font-bold">
                      {insp.FAILED_COUNT}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center text-gray-600">{insp.IMAGE_COUNT}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </main>
    </div>
  )
}

function StatCard({ label, value, color = 'blue' }) {
  const colors = {
    blue: 'text-blue-600',
    red: 'text-red-600',
    green: 'text-green-600',
    yellow: 'text-yellow-600',
  }
  return (
    <div className="bg-white rounded-lg shadow p-4">
      <p className="text-xs text-gray-500 uppercase tracking-wide">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${colors[color]}`}>{value}</p>
    </div>
  )
}
