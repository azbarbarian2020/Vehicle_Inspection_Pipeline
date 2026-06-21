import { useState, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'

export default function App() {
  const [inspections, setInspections] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [filters, setFilters] = useState({
    company: [],
    inspector: [],
    status: [],
    fleet: [],
  })

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

  // Extract unique values for filter dropdowns
  const filterOptions = useMemo(() => ({
    company: [...new Set(inspections.map(i => i.COMPANY).filter(Boolean))].sort(),
    inspector: [...new Set(inspections.map(i => i.INSPECTOR).filter(Boolean))].sort(),
    status: [...new Set(inspections.map(i => i.STATUS).filter(Boolean))].sort(),
    fleet: [...new Set(inspections.map(i => i.FLEET).filter(Boolean))].sort(),
  }), [inspections])

  // Apply filters
  const filteredInspections = useMemo(() => {
    return inspections.filter(insp => {
      if (filters.company.length && !filters.company.includes(insp.COMPANY)) return false
      if (filters.inspector.length && !filters.inspector.includes(insp.INSPECTOR)) return false
      if (filters.status.length && !filters.status.includes(insp.STATUS)) return false
      if (filters.fleet.length && !filters.fleet.includes(insp.FLEET)) return false
      return true
    })
  }, [inspections, filters])

  const toggleFilter = (key, value) => {
    setFilters(prev => ({
      ...prev,
      [key]: prev[key].includes(value)
        ? prev[key].filter(v => v !== value)
        : [...prev[key], value]
    }))
  }

  const clearFilters = () => setFilters({ company: [], inspector: [], status: [], fleet: [] })
  const hasActiveFilters = Object.values(filters).some(f => f.length > 0)

  if (loading) return <div className="p-8 text-center">Loading...</div>

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">Vehicle Inspection Dashboard</h1>
            <p className="text-sm text-gray-500 mt-1">Automated inspection report processing</p>
          </div>
          <Link to="/settings" className="text-sm text-gray-600 hover:text-blue-600 px-3 py-2 rounded border border-gray-200 hover:border-blue-300">
            Settings
          </Link>
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

      {/* Filters */}
      <div className="max-w-7xl mx-auto px-6 py-2">
        <div className="flex flex-wrap gap-3 items-center">
          <span className="text-sm font-medium text-gray-600">Filters:</span>
          <MultiFilter label="Company" options={filterOptions.company} selected={filters.company} onToggle={(v) => toggleFilter('company', v)} />
          <MultiFilter label="Inspector" options={filterOptions.inspector} selected={filters.inspector} onToggle={(v) => toggleFilter('inspector', v)} />
          <MultiFilter label="Status" options={filterOptions.status} selected={filters.status} onToggle={(v) => toggleFilter('status', v)} />
          <MultiFilter label="Fleet" options={filterOptions.fleet} selected={filters.fleet} onToggle={(v) => toggleFilter('fleet', v)} />
          {hasActiveFilters && (
            <button onClick={clearFilters} className="text-xs text-red-600 hover:text-red-800 px-2 py-1 border border-red-200 rounded hover:bg-red-50">
              Clear All
            </button>
          )}
          {hasActiveFilters && (
            <span className="text-xs text-gray-500">Showing {filteredInspections.length} of {inspections.length}</span>
          )}
        </div>
      </div>

      <main className="max-w-7xl mx-auto px-6 py-4">
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Inspection #</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Company</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Fleet</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Unit #</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Inspector</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Complete Date</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Status</th>
                <th className="px-4 py-3 text-center font-semibold text-gray-600">Failures</th>
                <th className="px-4 py-3 text-center font-semibold text-gray-600">Images</th>
                <th className="px-4 py-3 text-left font-semibold text-gray-600">Email Sent</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredInspections.map(insp => (
                <tr key={insp.INSPECTION_ID} className="hover:bg-blue-50 cursor-pointer">
                  <td className="px-4 py-3">
                    <Link to={`/inspection/${insp.INSPECTION_ID}`} className="text-blue-600 font-medium hover:underline">
                      #{insp.INSPECTION_NUM}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-gray-700">{insp.COMPANY}</td>
                  <td className="px-4 py-3 text-gray-700">{insp.FLEET}</td>
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
                  <td className="px-4 py-3 text-gray-500 text-xs">
                    {insp.EMAIL_SENT_AT ? formatDate(insp.EMAIL_SENT_AT) : <span className="text-yellow-600">Pending</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {filteredInspections.length === 0 && (
            <div className="p-8 text-center text-gray-500">
              {hasActiveFilters ? 'No inspections match the selected filters.' : 'No inspections processed yet.'}
            </div>
          )}
        </div>
      </main>
    </div>
  )
}

function formatDate(dateStr) {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function MultiFilter({ label, options, selected, onToggle }) {
  const [open, setOpen] = useState(false)

  if (options.length === 0) return null

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`text-xs px-3 py-1.5 rounded border flex items-center gap-1 ${
          selected.length > 0
            ? 'border-blue-400 bg-blue-50 text-blue-700'
            : 'border-gray-200 text-gray-600 hover:border-gray-300'
        }`}
      >
        {label}
        {selected.length > 0 && (
          <span className="bg-blue-600 text-white rounded-full px-1.5 text-[10px] leading-4">{selected.length}</span>
        )}
        <svg className="w-3 h-3 ml-1" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute z-20 mt-1 bg-white border border-gray-200 rounded-lg shadow-lg py-1 min-w-[180px] max-h-60 overflow-y-auto">
            {options.map(opt => (
              <label key={opt} className="flex items-center px-3 py-1.5 hover:bg-gray-50 cursor-pointer text-xs">
                <input
                  type="checkbox"
                  checked={selected.includes(opt)}
                  onChange={() => onToggle(opt)}
                  className="mr-2 rounded border-gray-300"
                />
                <span className="truncate">{opt}</span>
              </label>
            ))}
          </div>
        </>
      )}
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
