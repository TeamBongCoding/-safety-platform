import { useCallback, useEffect, useMemo, useState } from 'react'

const apiHostname = window.location.hostname || 'localhost'
const API_BASE = import.meta.env.VITE_API_BASE || `http://${apiHostname}:8000`
const WS_BASE = import.meta.env.VITE_WS_BASE || `ws://${apiHostname}:8000/ws`

const levelStyles = {
  ok: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-300',
  warn: 'border-amber-500/30 bg-amber-500/10 text-amber-300',
  alert: 'border-red-500/30 bg-red-500/10 text-red-300',
}

const eventLabels = {
  no_helmet: '안전모 미착용',
  zone_intrusion: '위험구역 침입',
}

export default function App() {
  const [connected, setConnected] = useState(false)
  const [summary, setSummary] = useState(null)
  const [events, setEvents] = useState([])
  const [streamReady, setStreamReady] = useState(false)

  const loadEvents = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/api/events?limit=12`)
      if (response.ok) setEvents(await response.json())
    } catch {
      // WebSocket 연결 상태가 전체 연결 상태를 표시한다.
    }
  }, [])

  useEffect(() => {
    let socket
    let reconnectTimer
    let disposed = false

    const connect = () => {
      socket = new WebSocket(WS_BASE)
      socket.onopen = () => setConnected(true)
      socket.onmessage = (event) => setSummary(JSON.parse(event.data))
      socket.onerror = () => socket.close()
      socket.onclose = () => {
        setConnected(false)
        if (!disposed) reconnectTimer = window.setTimeout(connect, 1500)
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  useEffect(() => {
    const initialTimer = window.setTimeout(loadEvents, 0)
    const timer = window.setInterval(loadEvents, 3000)
    return () => {
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [loadEvents])

  const resolveEvent = async (eventId) => {
    await fetch(`${API_BASE}/api/events/${eventId}/resolve`, { method: 'POST' })
    loadEvents()
  }

  const analysisLabel = useMemo(() => {
    if (!connected) return '서버 연결 대기'
    if (summary?.analysis_stage === 'loading') return 'AI 모델 준비 중'
    if (summary?.analysis_stage === 'error') return '분석 오류'
    if (summary?.analysis_running) return '실시간 분석 중'
    return '분석 중지됨'
  }, [connected, summary])

  return (
    <main className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="mb-5 flex flex-col gap-4 border-b border-slate-800 pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="mb-1 text-xs font-semibold tracking-[0.22em] text-cyan-400">SITE SAFETY OPERATIONS</p>
            <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">AI 안전관리 관제센터</h1>
            <p className="mt-1 text-sm text-slate-400">영상 분석, 안전장비 상태 및 현장 경고를 실시간으로 통합합니다.</p>
          </div>
          <div className="flex items-center gap-3 rounded-full border border-slate-700 bg-slate-900/80 px-4 py-2 text-sm">
            <span className={`h-2.5 w-2.5 rounded-full ${connected ? 'live-dot bg-emerald-400' : 'bg-red-400'}`} />
            <span className={connected ? 'text-emerald-300' : 'text-red-300'}>{analysisLabel}</span>
            <span className="text-slate-600">|</span>
            <span className="tabular-nums text-slate-400">{summary?.processing_fps ?? 0} FPS</span>
          </div>
        </header>

        <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="현재 작업 인원" value={summary?.worker_count} unit="명" tone="cyan" />
          <MetricCard label="안전모 미착용" value={summary?.no_helmet_count} unit="명" tone="red" />
          <MetricCard label="고리 미체결" value={summary?.unsecured_count} unit="명" tone="amber" />
          <MetricCard label="금일 위반" value={summary?.violations_today} unit="건" tone="violet" />
        </section>

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,0.75fr)]">
          <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/20">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
              <div>
                <h2 className="font-semibold text-white">현장 분석 영상</h2>
                <p className="text-xs text-slate-500">사람 감지 · 안전모 · 안전고리 · 위험구역 판정</p>
              </div>
              <span className="rounded-md bg-red-500/15 px-2.5 py-1 text-xs font-bold tracking-wider text-red-300">LIVE</span>
            </div>
            <div className="relative aspect-video bg-black">
              <img
                className="h-full w-full object-contain"
                src={`${API_BASE}/api/analysis/stream`}
                alt="AI가 실시간으로 분석 중인 현장 영상"
                onLoad={() => setStreamReady(true)}
                onError={() => setStreamReady(false)}
              />
              {!streamReady && (
                <div className="absolute inset-0 grid place-items-center bg-slate-950/95 text-center">
                  <div>
                    <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-slate-700 border-t-cyan-400" />
                    <p className="font-medium text-slate-300">{summary?.analysis_message ?? '영상 스트림을 기다리고 있습니다.'}</p>
                    {summary?.last_error && <p className="mt-2 max-w-md text-sm text-red-300">{summary.last_error}</p>}
                  </div>
                </div>
              )}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-5 py-3 text-xs text-slate-500">
              <span>프레임 #{summary?.frame_index ?? 0}</span>
              <span>고리 장치 {summary?.harness?.online ? '온라인' : '오프라인'}</span>
            </div>
          </div>

          <aside className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-white">작업자 상태</h2>
                <p className="text-xs text-slate-500">감지된 인원별 보호구 판정</p>
              </div>
              <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-400">{summary?.workers?.length ?? 0}명</span>
            </div>
            <div className="max-h-[510px] space-y-3 overflow-y-auto pr-1">
              {summary?.workers?.length ? summary.workers.map((worker) => (
                <WorkerCard key={worker.id} worker={worker} />
              )) : (
                <EmptyState text="현재 감지된 작업자가 없습니다." />
              )}
            </div>
          </aside>
        </section>

        <section className="mt-5 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
            <div>
              <h2 className="font-semibold text-white">최근 안전 이벤트</h2>
              <p className="text-xs text-slate-500">SQLite 데이터베이스에 저장된 분석 경고 이력</p>
            </div>
            <button onClick={loadEvents} className="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:border-cyan-500 hover:text-cyan-300">새로고침</button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead className="bg-slate-950/50 text-xs uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-5 py-3 font-medium">발생 시각</th>
                  <th className="px-5 py-3 font-medium">경고 유형</th>
                  <th className="px-5 py-3 font-medium">구역</th>
                  <th className="px-5 py-3 font-medium">신뢰도</th>
                  <th className="px-5 py-3 font-medium">상태</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {events.length ? events.map((event) => (
                  <tr key={event.id} className="text-slate-300 hover:bg-slate-800/40">
                    <td className="whitespace-nowrap px-5 py-3 text-slate-400">{formatDate(event.timestamp)}</td>
                    <td className="px-5 py-3 font-medium text-red-300">{eventLabels[event.event_type] ?? event.event_type}</td>
                    <td className="px-5 py-3">{event.zone_id ? `구역 ${event.zone_id}` : '일반구역'}</td>
                    <td className="px-5 py-3 tabular-nums">{Math.round(event.confidence * 100)}%</td>
                    <td className="px-5 py-3">
                      {event.resolved ? (
                        <span className="text-emerald-400">조치 완료</span>
                      ) : (
                        <button onClick={() => resolveEvent(event.id)} className="rounded-md bg-red-500/15 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/25">조치 처리</button>
                      )}
                    </td>
                  </tr>
                )) : (
                  <tr><td colSpan="5" className="px-5 py-10 text-center text-slate-500">저장된 안전 이벤트가 없습니다.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  )
}

function MetricCard({ label, value, unit, tone }) {
  const tones = {
    cyan: 'from-cyan-500/15 to-cyan-500/5 text-cyan-300',
    red: 'from-red-500/15 to-red-500/5 text-red-300',
    amber: 'from-amber-500/15 to-amber-500/5 text-amber-300',
    violet: 'from-violet-500/15 to-violet-500/5 text-violet-300',
  }
  return (
    <div className={`rounded-xl border border-slate-800 bg-gradient-to-br p-4 ${tones[tone]}`}>
      <p className="text-xs font-medium text-slate-400">{label}</p>
      <p className="mt-2 text-3xl font-bold tabular-nums text-white">{value ?? '-'} <span className="text-sm font-medium text-slate-500">{unit}</span></p>
    </div>
  )
}

function WorkerCard({ worker }) {
  const style = levelStyles[worker.level] ?? levelStyles.ok
  return (
    <article className={`rounded-xl border p-4 ${style}`}>
      <div className="mb-3 flex items-center justify-between">
        <span className="font-semibold text-white">{worker.id}</span>
        <span className="text-xs font-bold uppercase tracking-wider">{worker.level}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <StatusPill label="안전모" ok={worker.helmet_on} />
        <StatusPill label="안전고리" ok={worker.hook_closed} />
      </div>
      <p className="mt-3 text-xs text-slate-400">위치 · {worker.zone}</p>
      {worker.reasons?.length > 0 && <p className="mt-1 text-xs font-medium">{worker.reasons.join(' · ')}</p>}
    </article>
  )
}

function StatusPill({ label, ok }) {
  return (
    <span className={`rounded-md px-2 py-1.5 ${ok ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300'}`}>
      {label} {ok ? '정상' : '미확인'}
    </span>
  )
}

function EmptyState({ text }) {
  return <div className="rounded-xl border border-dashed border-slate-700 px-4 py-10 text-center text-sm text-slate-500">{text}</div>
}

function formatDate(value) {
  return new Intl.DateTimeFormat('ko-KR', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}
