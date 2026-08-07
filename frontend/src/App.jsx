import { useCallback, useEffect, useMemo, useState } from 'react'
import AdminDashboard from './AdminDashboard'
import BrowserCameraController from './BrowserCameraController'
import ZoneEditor from './ZoneEditor'
import { API_BASE, WS_URL, api } from './api'

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
  const [session, setSession] = useState(undefined)
  const [sessionError, setSessionError] = useState('')

  useEffect(() => {
    api('/api/auth/me')
      .then(setSession)
      .catch((error) => {
        if (error.status !== 401) setSessionError(error.message)
        setSession(null)
      })
  }, [])

  if (session === undefined) return <LoadingScreen />
  if (!session) {
    return <AuthScreen onAuthenticated={setSession} initialError={sessionError} />
  }
  if (session.user.role === 'platform_admin') {
    return <AdminDashboard session={session} setSession={setSession} />
  }
  return <Dashboard session={session} setSession={setSession} />
}

function AuthScreen({ onAuthenticated, initialError }) {
  const [mode, setMode] = useState('login')
  const [error, setError] = useState(initialError)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setSubmitting(true)
    const form = new FormData(event.currentTarget)
    const payload = Object.fromEntries(form.entries())

    try {
      const nextSession = await api(`/api/auth/${mode}`, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      onAuthenticated(nextSession)
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-[#07111f] px-4 py-10 text-slate-100">
      <div className="w-full max-w-md">
        <div className="mb-7 text-center">
          <p className="mb-2 text-xs font-semibold tracking-[0.24em] text-cyan-400">SITE SAFETY OPERATIONS</p>
          <h1 className="text-3xl font-bold text-white">AI 안전관리 플랫폼</h1>
          <p className="mt-2 text-sm text-slate-400">내 현장의 작업자와 위험 상황을 안전하게 관리하세요.</p>
        </div>

        <section className="rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl shadow-black/30">
          <div className="mb-6 grid grid-cols-2 rounded-xl bg-slate-950 p-1">
            <AuthTab active={mode === 'login'} onClick={() => { setMode('login'); setError('') }}>로그인</AuthTab>
            <AuthTab active={mode === 'signup'} onClick={() => { setMode('signup'); setError('') }}>회원가입</AuthTab>
          </div>

          <form className="space-y-4" onSubmit={submit}>
            {mode === 'signup' && (
              <>
                <Field label="회사명" name="company_name" placeholder="예: 세이프 건설" required />
                <Field label="담당자명" name="manager_name" placeholder="예: 김안전" required />
                <Field label="첫 현장명" name="site_name" placeholder="예: 서울 물류센터" required />
              </>
            )}
            <Field label="이메일" name="email" type="email" placeholder="manager@company.com" autoComplete="email" required />
            <Field
              label="비밀번호"
              name="password"
              type="password"
              minLength="8"
              placeholder="8자 이상 입력"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
            />

            {error && <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{error}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-xl bg-cyan-500 px-4 py-3 font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-wait disabled:opacity-60"
            >
              {submitting ? '처리 중...' : mode === 'login' ? '관제센터 로그인' : '계정 만들기'}
            </button>
          </form>
        </section>
      </div>
    </main>
  )
}

function Dashboard({ session, setSession }) {
  const currentSite = session.current_site
  const [connected, setConnected] = useState(false)
  const [summary, setSummary] = useState(null)
  const [events, setEvents] = useState([])
  const [streamReady, setStreamReady] = useState(false)
  const [newSiteName, setNewSiteName] = useState('')
  const [addingSite, setAddingSite] = useState(false)
  const [notice, setNotice] = useState('')
  const [activeCameraId, setActiveCameraId] = useState(null)

  const handleUnauthorized = useCallback((error) => {
    if (error.status === 401) setSession(null)
    else setNotice(error.message)
  }, [setSession])

  const loadEvents = useCallback(async () => {
    if (!currentSite) return
    try {
      setEvents(await api('/api/events?limit=12'))
    } catch (error) {
      handleUnauthorized(error)
    }
  }, [currentSite, handleUnauthorized])

  useEffect(() => {
    if (!currentSite) return undefined
    let socket
    let reconnectTimer
    let disposed = false

    const connect = () => {
      const summaryUrl = activeCameraId ? `${WS_URL}?camera_id=${activeCameraId}` : WS_URL
      socket = new WebSocket(summaryUrl)
      socket.onopen = () => setConnected(true)
      socket.onmessage = (event) => setSummary(JSON.parse(event.data))
      socket.onerror = () => socket.close()
      socket.onclose = (event) => {
        setConnected(false)
        if (event.code === 4401) setSession(null)
        else if (!disposed) reconnectTimer = window.setTimeout(connect, 1500)
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [activeCameraId, currentSite, setSession])

  useEffect(() => {
    const initialTimer = window.setTimeout(loadEvents, 0)
    const timer = window.setInterval(loadEvents, 3000)
    return () => {
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [loadEvents])

  const selectSite = async (siteId) => {
    try {
      const nextSession = await api(`/api/sites/${siteId}/select`, { method: 'POST' })
      setSummary(null)
      setEvents([])
      setStreamReady(false)
      setActiveCameraId(null)
      setSession(nextSession)
      setNotice('')
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const createSite = async (event) => {
    event.preventDefault()
    if (!newSiteName.trim()) return
    try {
      const nextSession = await api('/api/sites', {
        method: 'POST',
        body: JSON.stringify({ name: newSiteName.trim() }),
      })
      setSummary(null)
      setEvents([])
      setStreamReady(false)
      setActiveCameraId(null)
      setSession(nextSession)
      setNewSiteName('')
      setAddingSite(false)
      setNotice('새 현장을 만들고 현재 현장으로 선택했습니다.')
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const logout = async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
    } finally {
      setSession(null)
    }
  }

  const resolveEvent = async (eventId) => {
    try {
      await api(`/api/events/${eventId}/resolve`, { method: 'POST' })
      loadEvents()
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const analysisLabel = useMemo(() => {
    if (!connected) return '서버 연결 대기'
    if (summary?.analysis_stage === 'loading') return 'AI 모델 준비 중'
    if (summary?.analysis_stage === 'waiting_camera' || summary?.analysis_stage === 'waiting_frame') return '카메라 연결 대기'
    if (summary?.analysis_stage === 'camera_disconnected') return '카메라 연결 종료'
    if (summary?.analysis_stage === 'error') return '분석 오류'
    if (summary?.analysis_running) return '실시간 분석 중'
    return '분석 중지됨'
  }, [connected, summary])

  return (
    <main className="min-h-screen bg-[#07111f] text-slate-100">
      <div className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="mb-5 border-b border-slate-800 pb-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
            <div>
              <p className="mb-1 text-xs font-semibold tracking-[0.22em] text-cyan-400">SITE SAFETY OPERATIONS</p>
              <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">AI 안전관리 관제센터</h1>
              <p className="mt-1 text-sm text-slate-400">{session.user.company_name} · 담당자 {session.user.manager_name}</p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <label className="sr-only" htmlFor="site-select">관리 현장</label>
              <select
                id="site-select"
                value={currentSite?.id ?? ''}
                onChange={(event) => selectSite(Number(event.target.value))}
                className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
              >
                {session.sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
              </select>
              <button onClick={() => setAddingSite((value) => !value)} className="rounded-lg border border-cyan-500/40 px-3 py-2 text-sm text-cyan-300 hover:bg-cyan-500/10">현장 추가</button>
              <button onClick={logout} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:border-red-500/50 hover:text-red-300">로그아웃</button>
            </div>
          </div>

          {addingSite && (
            <form onSubmit={createSite} className="mt-4 flex max-w-lg gap-2">
              <input
                value={newSiteName}
                onChange={(event) => setNewSiteName(event.target.value)}
                placeholder="새 현장명"
                maxLength="100"
                autoFocus
                className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
              />
              <button className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950">추가</button>
            </form>
          )}
          {notice && <p className="mt-3 text-sm text-cyan-300">{notice}</p>}
        </header>

        <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-medium text-slate-500">현재 관리 현장</p>
            <h2 className="text-lg font-semibold text-white">{currentSite?.name}</h2>
          </div>
          <div className="flex items-center gap-3 rounded-full border border-slate-700 bg-slate-900/80 px-4 py-2 text-sm">
            <span className={`h-2.5 w-2.5 rounded-full ${connected ? 'live-dot bg-emerald-400' : 'bg-red-400'}`} />
            <span className={connected ? 'text-emerald-300' : 'text-red-300'}>{analysisLabel}</span>
            <span className="text-slate-600">|</span>
            <span className="tabular-nums text-slate-400">{summary?.processing_fps ?? 0} FPS</span>
          </div>
        </div>

        <BrowserCameraController
          key={currentSite?.id}
          site={currentSite}
          onCameraChange={(cameraId) => {
            setActiveCameraId(cameraId)
            setSummary(null)
            setStreamReady(false)
          }}
        />

        <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <MetricCard label="현재 작업 인원" value={summary?.worker_count} unit="명" tone="cyan" />
          <MetricCard label="안전모 미착용" value={summary?.no_helmet_count} unit="명" tone="red" />
          <MetricCard label="카메라 전환 대기" value={summary?.transition_candidate_count} unit="명" tone="amber" />
          <MetricCard label="금일 위반" value={summary?.violations_today} unit="건" tone="violet" />
        </section>

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,0.75fr)]">
          <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/20">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
              <div>
                <h2 className="font-semibold text-white">현장 분석 영상</h2>
                <p className="text-xs text-slate-500">{currentSite?.name} · {activeCameraId ? '클라이언트 라이브 카메라' : '기본 분석 영상'} · 사람 추적 · Re-ID · 안전모 · 위험구역 판정</p>
              </div>
              <span className="rounded-md bg-red-500/15 px-2.5 py-1 text-xs font-bold tracking-wider text-red-300">LIVE</span>
            </div>
            <ZoneEditor
              key={`${currentSite?.id}-${activeCameraId ?? 'default'}`}
              siteId={currentSite?.id}
              cameraId={activeCameraId}
              streamKey={`${currentSite?.id}-${activeCameraId ?? 'default'}`}
              streamSrc={`${API_BASE}/api/analysis/stream${activeCameraId ? `?camera_id=${activeCameraId}` : ''}`}
              streamAlt={`${currentSite?.name} AI 실시간 분석 영상`}
              streamReady={streamReady}
              waitingMessage={summary?.analysis_message ?? '영상 스트림을 기다리고 있습니다.'}
              streamError={summary?.last_error}
              onStreamLoad={() => setStreamReady(true)}
              onStreamError={() => setStreamReady(false)}
              onRequestError={handleUnauthorized}
            />
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-5 py-3 text-xs text-slate-500">
              <span>프레임 #{summary?.frame_index ?? 0}</span>
              <span className={(summary?.entry_roi_count ?? 0) > 0 && (summary?.exit_roi_count ?? 0) > 0 ? '' : 'text-amber-300'}>
                {activeCameraId ? `카메라 #${activeCameraId}` : '기본 영상'} · {summary?.reid_backend === 'fastreid' ? 'FastReID' : summary?.reid_backend ? 'Fallback Re-ID' : 'Re-ID 준비'} · 입구 ROI {summary?.entry_roi_count ?? 0} · 출구 ROI {summary?.exit_roi_count ?? 0}
              </span>
            </div>
          </div>

          <aside className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h2 className="font-semibold text-white">작업자 상태</h2>
                <p className="text-xs text-slate-500">현재 현장에서 감지된 인원</p>
              </div>
              <span className="rounded-full bg-slate-800 px-2.5 py-1 text-xs text-slate-400">{summary?.workers?.length ?? 0}명</span>
            </div>
            <div className="max-h-[510px] space-y-3 overflow-y-auto pr-1">
              {summary?.workers?.length ? summary.workers.map((worker) => (
                <WorkerCard key={worker.id} worker={worker} />
              )) : <EmptyState text="현재 감지된 작업자가 없습니다." />}
            </div>
          </aside>
        </section>

        <section className="mt-5 overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
          <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
            <div>
              <h2 className="font-semibold text-white">최근 안전 이벤트</h2>
              <p className="text-xs text-slate-500">{currentSite?.name}에 저장된 분석 경고 이력</p>
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
                      {event.resolved ? <span className="text-emerald-400">조치 완료</span> : (
                        <button onClick={() => resolveEvent(event.id)} className="rounded-md bg-red-500/15 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/25">조치 처리</button>
                      )}
                    </td>
                  </tr>
                )) : <tr><td colSpan="5" className="px-5 py-10 text-center text-slate-500">현재 현장에 저장된 안전 이벤트가 없습니다.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  )
}

function Field({ label, ...props }) {
  return (
    <label className="block text-sm text-slate-300">
      <span className="mb-1.5 block font-medium">{label}</span>
      <input {...props} className="w-full rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-3 text-white outline-none transition placeholder:text-slate-600 focus:border-cyan-500 focus:ring-2 focus:ring-cyan-500/10" />
    </label>
  )
}

function AuthTab({ active, onClick, children }) {
  return <button type="button" onClick={onClick} className={`rounded-lg px-3 py-2 text-sm font-medium transition ${active ? 'bg-slate-800 text-white shadow' : 'text-slate-500 hover:text-slate-300'}`}>{children}</button>
}

function LoadingScreen() {
  return (
    <main className="grid min-h-screen place-items-center bg-[#07111f] text-slate-300">
      <div className="text-center"><div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-slate-700 border-t-cyan-400" /><p>로그인 상태 확인 중...</p></div>
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
        <span className="font-semibold text-white">Global ID · {worker.global_person_id ?? worker.id}</span>
        <span className="text-xs font-bold uppercase tracking-wider">{worker.level}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <StatusPill label="안전모" ok={worker.helmet_on} />
        <span className="rounded-md bg-slate-950/30 px-2 py-1.5 text-slate-300">카메라 내부 ID · {worker.local_track_id}</span>
      </div>
      <p className="mt-3 text-xs text-slate-400">위치 · {worker.zone}</p>
      <p className="mt-1 text-xs text-slate-400">객체 품질 · {Math.round((worker.image_quality ?? 0) * 100)}%</p>
      {worker.camera_transition && (
        <p className="mt-2 rounded-md bg-cyan-500/10 px-2 py-1.5 text-xs text-cyan-200">
          카메라 #{worker.camera_transition.matched_from_camera_id}에서 연결 · Re-ID {Math.round(worker.camera_transition.reid_similarity * 100)}% · 종합 {Math.round(worker.camera_transition.match_score * 100)}% · {worker.camera_transition.transition_seconds}초
        </p>
      )}
      {worker.reid_pending && !worker.camera_transition && (
        <p className="mt-2 rounded-md bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200">입구 ROI 전환 후보 비교 중</p>
      )}
      {worker.reasons?.length > 0 && <p className="mt-1 text-xs font-medium">{worker.reasons.join(' · ')}</p>}
    </article>
  )
}

function StatusPill({ label, ok }) {
  return <span className={`rounded-md px-2 py-1.5 ${ok ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300'}`}>{label} {ok ? '정상' : '미확인'}</span>
}

function EmptyState({ text }) {
  return <div className="rounded-xl border border-dashed border-slate-700 px-4 py-10 text-center text-sm text-slate-500">{text}</div>
}

function formatDate(value) {
  return new Intl.DateTimeFormat('ko-KR', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}
