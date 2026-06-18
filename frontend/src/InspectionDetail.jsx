import { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'

export default function InspectionDetail() {
  const { id } = useParams()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`/api/inspections/${id}`)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false) })
      .catch(() => setLoading(false))
  }, [id])

  if (loading) return <div className="p-8 text-center">Loading...</div>
  if (!data) return <div className="p-8 text-center text-red-600">Inspection not found</div>

  const { summary, failed_items, images } = data
  const imagesByItem = {}
  images.forEach(img => {
    if (!imagesByItem[img.ITEM_ID]) imagesByItem[img.ITEM_ID] = []
    imagesByItem[img.ITEM_ID].push(img)
  })

  return (
    <div className="min-h-screen">
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
          <Link to="/" className="text-blue-600 hover:text-blue-800 text-sm">&larr; All Inspections</Link>
          <h1 className="text-xl font-bold text-gray-800">Inspection #{summary.INSPECTION_NUM}</h1>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6 space-y-6">
        {/* Summary Card */}
        <div className="bg-white rounded-lg shadow p-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-4">Summary Information</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4 text-sm">
            <Field label="Company" value={summary.COMPANY} />
            <Field label="Fleet" value={summary.FLEET} />
            <Field label="Unit #" value={summary.UNIT_NUM} />
            <Field label="Serial #" value={summary.SERIAL_NUM} />
            <Field label="Model #" value={summary.MODEL_NUM} />
            <Field label="Inspector" value={summary.INSPECTOR} />
            <Field label="Order Date" value={summary.ORDER_DATE} />
            <Field label="Complete Date" value={summary.COMPLETE_DATE} />
            <Field label="Status" value={summary.STATUS} />
            <Field label="Location" value={summary.LOCATION} />
            <Field label="Trouble Ticket" value={summary.TROUBLE_TICKET} />
            <Field label="Invoice #" value={summary.INVOICE_NUM} />
          </div>
        </div>

        {/* Failed Items */}
        <div>
          <h2 className="text-lg font-semibold text-gray-800 mb-3">
            Failed Items <span className="text-red-600">({failed_items.length})</span>
          </h2>
          <div className="space-y-4">
            {failed_items.map(item => (
              <div key={item.ITEM_ID} className="bg-white rounded-lg shadow border-l-4 border-red-500 p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <h3 className="font-semibold text-gray-800">
                      <span className="text-red-600 font-mono mr-2">{item.LINE_NUM}</span>
                      {item.DESCRIPTION}
                    </h3>
                    {item.COMMENTS && (
                      <p className="text-gray-600 mt-1 text-sm">{item.COMMENTS}</p>
                    )}
                  </div>
                </div>
                {/* Images for this item */}
                {imagesByItem[item.ITEM_ID] && imagesByItem[item.ITEM_ID].length > 0 && (
                  <div className="mt-3 flex flex-wrap gap-3">
                    {imagesByItem[item.ITEM_ID].map(img => (
                      <a key={img.IMAGE_ID} href={img.IMAGE_URL} target="_blank" rel="noopener noreferrer">
                        <img
                          src={img.IMAGE_URL}
                          alt={`Failure ${item.LINE_NUM} photo ${img.IMAGE_SEQUENCE}`}
                          className="h-48 w-auto rounded border border-gray-200 shadow-sm hover:shadow-md transition-shadow object-cover"
                        />
                      </a>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </main>
    </div>
  )
}

function Field({ label, value }) {
  return (
    <div>
      <span className="text-gray-500 text-xs uppercase tracking-wide">{label}</span>
      <p className="text-gray-800 font-medium">{value || 'N/A'}</p>
    </div>
  )
}
