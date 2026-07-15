import { useEffect, useState } from 'react'

interface HealthResponse {
  status: string
  cards_loaded: number
}

function HealthStatus() {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/health')
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return res.json() as Promise<HealthResponse>
      })
      .then(setHealth)
      .catch((err: unknown) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
  }, [])

  if (error) return <p>API error: {error}</p>
  if (!health) return <p>Checking API…</p>
  return (
    <p>
      API status: {health.status} — cards loaded: {health.cards_loaded}
    </p>
  )
}

function App() {
  return (
    <main>
      <h1>Commander Deckbuilder</h1>
      <HealthStatus />
    </main>
  )
}

export default App
