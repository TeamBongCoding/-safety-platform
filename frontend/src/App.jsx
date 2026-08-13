import { useCallback, useEffect, useMemo, useState } from 'react'
import AdminDashboard from './AdminDashboard'
import BrowserCameraController from './BrowserCameraController'
import RankingDashboard from './RankingDashboard'
import RiskDashboard from './RiskDashboard'
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
  zone_approach: '출입금지구역 접근',
  fall_risk_entry: '추락위험 구역 진입',
  fall_risk_approach: '추락위험 구역 접근',
  heavy_equipment_entry: '중장비 작업반경 진입',
  heavy_equipment_approach: '중장비 작업반경 접근',
  stagger: '휘청거림 감지',
  sudden_sit: '주저앉음 감지',
  fall: '쓰러짐 감지',
  fall_still: '쓰러짐+장시간 정지',
  heat_stagger: '폭염 휘청거림 감지',
  heat_sudden_sit: '폭염 주저앉음 감지',
  heat_fall: '폭염 쓰러짐 감지',
  heat_fall_still: '폭염 쓰러짐+장시간 정지',
}

const behaviorStyles = {
  STAGGER:    'bg-amber-500/15 text-amber-300',
  SUDDEN_SIT: 'bg-orange-500/15 text-orange-300',
  FALL:       'bg-red-500/20 text-red-300',
  FALL_STILL: 'bg-red-600/30 text-red-200',
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
  return <UserExperience session={session} setSession={setSession} />
}

function UserExperience({ session, setSession }) {
  const [screen, setScreen] = useState('dashboard')
  if (screen === 'ranking') {
    return (
      <main className="min-h-screen bg-[#07111f] px-4 py-6 text-slate-100 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1400px]">
          <RankingDashboard
            currentCompany={session.user.company_name}
            onBack={() => setScreen('dashboard')}
          />
        </div>
      </main>
    )
  }
  if (screen === 'risk') {
    return (
      <main className="min-h-screen bg-[#07111f] px-4 py-6 text-slate-100 sm:px-6 lg:px-8">
        <div className="mx-auto max-w-[1400px]">
          <RiskDashboard onBack={() => setScreen('dashboard')} />
        </div>
      </main>
    )
  }
  return <Dashboard session={session} setSession={setSession} onShowRanking={() => setScreen('ranking')} onShowRisk={() => setScreen('risk')} />
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

const heatLevelMeta = {
  inactive: null,
  caution: { label: '폭염 주의', color: 'amber', bg: 'bg-amber-500/10', border: 'border-amber-500/30', text: 'text-amber-300', badge: 'bg-amber-500/20 text-amber-200' },
  warning: { label: '폭염 경보', color: 'orange', bg: 'bg-orange-500/10', border: 'border-orange-500/30', text: 'text-orange-300', badge: 'bg-orange-500/20 text-orange-200' },
  severe: { label: '폭염 심각', color: 'red', bg: 'bg-red-500/10', border: 'border-red-500/30', text: 'text-red-300', badge: 'bg-red-500/20 text-red-200' },
}

function Dashboard({ session, setSession, onShowRanking, onShowRisk }) {
  const currentSite = session.current_site
  const [connected, setConnected] = useState(false)
  const [summary, setSummary] = useState(null)
  const [events, setEvents] = useState([])
  const [streamReady, setStreamReady] = useState(false)
  const [newSiteName, setNewSiteName] = useState('')
  const [newSiteLat, setNewSiteLat] = useState('')
  const [newSiteLon, setNewSiteLon] = useState('')
  const [addingSite, setAddingSite] = useState(false)
  const [notice, setNotice] = useState('')
  const [heatStatus, setHeatStatus] = useState(null)
  const [demoTemp, setDemoTemp] = useState('')
  const [showDemoControls, setShowDemoControls] = useState(false)
  const [showCamSettings, setShowCamSettings] = useState(false)
  const [cameraStreaming, setCameraStreaming] = useState(false)
  const [confirmDeleteSite, setConfirmDeleteSite] = useState(false)
  const [deleteSiteId, setDeleteSiteId] = useState('')
  const [sunThreshold, setSunThreshold] = useState(1.15)
  const [shadeThreshold, setShadeThreshold] = useState(0.85)
  const [cautionTemp, setCautionTemp] = useState(31.0)
  const [warningTemp, setWarningTemp] = useState(33.0)
  const [severeTemp, setSevereTemp] = useState(38.0)

  const handleUnauthorized = useCallback((error) => {
    if (error.status === 401) setSession(null)
    else setNotice(error.message)
  }, [setSession])

  const handleStreamLoad = useCallback(() => setStreamReady(true), [])
  const handleStreamError = useCallback(() => setStreamReady(false), [])

  // 체감온도 상태 30초 폴링 — 서버 임계값으로 로컬 상태 초기화
  useEffect(() => {
    if (!currentSite) return undefined
    const fetchHeat = () => {
      api('/api/heat/status').then((data) => {
        setHeatStatus(data)
        if (data.caution_temp != null) setCautionTemp(data.caution_temp)
        if (data.warning_temp != null) setWarningTemp(data.warning_temp)
        if (data.severe_temp != null)  setSevereTemp(data.severe_temp)
        if (data.sun_threshold != null)   setSunThreshold(data.sun_threshold)
        if (data.shade_threshold != null) setShadeThreshold(data.shade_threshold)
      }).catch(() => {})
    }
    fetchHeat()
    const timer = window.setInterval(fetchHeat, 30_000)
    return () => window.clearInterval(timer)
  }, [currentSite])

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
      socket = new WebSocket(WS_URL)
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
  }, [currentSite, setSession])

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
      setSession(nextSession)
      setNotice('')
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const createSite = async (event) => {
    event.preventDefault()
    if (!newSiteName.trim()) return
    const body = { name: newSiteName.trim() }
    const lat = parseFloat(newSiteLat)
    const lon = parseFloat(newSiteLon)
    if (!isNaN(lat) && !isNaN(lon)) {
      body.latitude = lat
      body.longitude = lon
    }
    try {
      const nextSession = await api('/api/sites', {
        method: 'POST',
        body: JSON.stringify(body),
      })
      setSummary(null)
      setEvents([])
      setStreamReady(false)
      setSession(nextSession)
      setNewSiteName('')
      setNewSiteLat('')
      setNewSiteLon('')
      setAddingSite(false)
      setNotice('새 현장을 만들고 현재 현장으로 선택했습니다.')
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const applyDemoTemp = async () => {
    const val = demoTemp.trim() === '' ? null : parseFloat(demoTemp)
    if (demoTemp.trim() !== '' && isNaN(val)) return
    try {
      await api('/api/heat/demo', { method: 'PATCH', body: JSON.stringify({ apparent_temp: val }) })
      api('/api/heat/status').then(setHeatStatus).catch(() => {})
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const applyThresholds = async () => {
    try {
      await api('/api/heat/thresholds', {
        method: 'PATCH',
        body: JSON.stringify({ sun_threshold: sunThreshold, shade_threshold: shadeThreshold }),
      })
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const applyTempThresholds = async () => {
    try {
      await api('/api/heat/temp-thresholds', {
        method: 'PATCH',
        body: JSON.stringify({ caution_temp: cautionTemp, warning_temp: warningTemp, severe_temp: severeTemp }),
      })
      api('/api/heat/status').then(setHeatStatus).catch(() => {})
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const toggleOutdoor = async () => {
    if (!currentSite) return
    try {
      const updated = await api(`/api/sites/${currentSite.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_outdoor: !currentSite.is_outdoor }),
      })
      setSession((s) => ({
        ...s,
        current_site: updated,
        sites: s.sites.map((site) => (site.id === updated.id ? updated : site)),
      }))
    } catch (error) {
      handleUnauthorized(error)
    }
  }

  const deleteSite = async () => {
    const targetId = Number(deleteSiteId)
    const targetSite = session.sites.find((site) => site.id === targetId)
    if (!targetSite) return
    try {
      const nextSession = await api(`/api/sites/${targetId}`, { method: 'DELETE' })
      setSummary(null)
      setEvents([])
      setStreamReady(false)
      setConfirmDeleteSite(false)
      setDeleteSiteId('')
      setSession(nextSession)
      setNotice(`'${targetSite.name}' 현장을 삭제했습니다.`)
    } catch (error) {
      setConfirmDeleteSite(false)
      setDeleteSiteId('')
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
    if (summary?.analysis_running) return '영상 분석 중'
    return '분석 중지됨'
  }, [connected, summary])

  if (!currentSite) {
    return (
      <main className="grid min-h-screen place-items-center bg-[#07111f] px-4 py-10 text-slate-100">
        <section className="w-full max-w-xl rounded-2xl border border-slate-800 bg-slate-900 p-6 shadow-2xl shadow-black/30">
          <p className="text-xs font-semibold tracking-[0.22em] text-cyan-400">SITE SAFETY OPERATIONS</p>
          <h1 className="mt-2 text-2xl font-bold text-white">관리할 현장을 등록하세요</h1>
          <p className="mt-2 text-sm text-slate-400">현재 계정에 남아 있는 현장이 없습니다. 새 현장을 만들면 관제 화면이 바로 시작됩니다.</p>
          <form onSubmit={createSite} className="mt-6 grid gap-3 sm:grid-cols-2">
            <input
              value={newSiteName}
              onChange={(event) => setNewSiteName(event.target.value)}
              placeholder="새 현장명"
              maxLength="100"
              autoFocus
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500 sm:col-span-2"
            />
            <input
              value={newSiteLat}
              onChange={(event) => setNewSiteLat(event.target.value)}
              placeholder="위도 (선택)"
              type="number"
              step="any"
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
            />
            <input
              value={newSiteLon}
              onChange={(event) => setNewSiteLon(event.target.value)}
              placeholder="경도 (선택)"
              type="number"
              step="any"
              className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
            />
            <button disabled={!newSiteName.trim()} className="rounded-lg bg-cyan-500 px-4 py-2.5 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-40 sm:col-span-2">
              새 현장 만들기
            </button>
          </form>
          {notice && <p className="mt-3 text-sm text-red-300">{notice}</p>}
          <button onClick={logout} className="mt-5 text-sm text-slate-500 hover:text-red-300">로그아웃</button>
        </section>
      </main>
    )
  }

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
              {currentSite && (
                confirmDeleteSite ? (
                  <span className="flex flex-wrap items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/5 p-1.5">
                    <label className="sr-only" htmlFor="delete-site-select">삭제할 현장</label>
                    <select
                      id="delete-site-select"
                      value={deleteSiteId}
                      onChange={(event) => setDeleteSiteId(event.target.value)}
                      className="rounded-md border border-red-500/40 bg-slate-950 px-2.5 py-1.5 text-sm text-red-200 outline-none focus:border-red-400"
                    >
                      <option value="">삭제할 현장 선택</option>
                      {session.sites.map((site) => <option key={site.id} value={site.id}>{site.name}</option>)}
                    </select>
                    <button disabled={!deleteSiteId} onClick={deleteSite} className="rounded-lg border border-red-500/60 bg-red-500/15 px-3 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/25 disabled:opacity-40">선택 현장 삭제</button>
                    <button onClick={() => { setConfirmDeleteSite(false); setDeleteSiteId('') }} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:text-slate-200">취소</button>
                    {session.sites.length === 1 && <span className="px-1 text-xs text-red-400">마지막 현장 삭제 후 새 현장을 등록해야 합니다.</span>}
                  </span>
                ) : (
                  <button onClick={() => { setDeleteSiteId(String(currentSite.id)); setConfirmDeleteSite(true) }} className="rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-400/70 hover:border-red-500/60 hover:text-red-300">현장 삭제</button>
                )
              )}
              <button onClick={() => setShowCamSettings((v) => !v)} className={`rounded-lg border px-3 py-2 text-sm transition ${showCamSettings ? 'border-sky-500 bg-sky-500/10 text-sky-300' : 'border-slate-700 text-slate-400 hover:border-sky-500/50 hover:text-sky-300'}`}>카메라 설정</button>
              <button onClick={onShowRanking} className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-sm font-semibold text-emerald-300 hover:bg-emerald-500/20">오늘 안전 순위</button>
              <button onClick={onShowRisk} className="rounded-lg border border-violet-500/40 bg-violet-500/10 px-3 py-2 text-sm font-semibold text-violet-300 hover:bg-violet-500/20">위험 추세 분석</button>
              <button onClick={logout} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:border-red-500/50 hover:text-red-300">로그아웃</button>
            </div>
          </div>

          {addingSite && (
            <form onSubmit={createSite} className="mt-4 flex max-w-2xl flex-wrap gap-2">
              <input
                value={newSiteName}
                onChange={(event) => setNewSiteName(event.target.value)}
                placeholder="새 현장명"
                maxLength="100"
                autoFocus
                className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
              />
              <input
                value={newSiteLat}
                onChange={(event) => setNewSiteLat(event.target.value)}
                placeholder="위도 (선택)"
                type="number"
                step="any"
                className="w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
              />
              <input
                value={newSiteLon}
                onChange={(event) => setNewSiteLon(event.target.value)}
                placeholder="경도 (선택)"
                type="number"
                step="any"
                className="w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-cyan-500"
              />
              <button className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950">추가</button>
            </form>
          )}
          {showCamSettings && (
            <div className="mt-4 flex max-w-xl flex-col gap-3 rounded-xl border border-sky-500/30 bg-sky-500/5 px-5 py-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-sky-400">카메라 1 설정</p>
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium text-slate-200">야외 카메라</p>
                  <p className="text-xs text-slate-500">야외 촬영 시 폭염 모니터링 및 양지·그늘 감지가 활성화됩니다.</p>
                </div>
                <button
                  type="button"
                  onClick={toggleOutdoor}
                  className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${currentSite?.is_outdoor ? 'bg-sky-500' : 'bg-slate-700'}`}
                >
                  <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${currentSite?.is_outdoor ? 'translate-x-5' : 'translate-x-0'}`} />
                </button>
              </div>
              <p className="text-xs text-slate-600">
                현재: {currentSite?.is_outdoor ? '야외 카메라 (폭염 모니터링 활성)' : '실내 카메라 (폭염 모니터링 비활성)'}
              </p>
            </div>
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

        <section className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-3">
          <MetricCard label="현재 작업 인원" value={summary?.worker_count} unit="명" tone="cyan" />
          <MetricCard label="안전모 미착용" value={summary?.no_helmet_count} unit="명" tone="red" />
          <MetricCard label="금일 위반" value={summary?.violations_today} unit="건" tone="violet" />
        </section>

        <BrowserCameraController onStreamingChange={setCameraStreaming} />

        {heatStatus && (
          <HeatSection
            heatStatus={heatStatus}
            workers={summary?.workers ?? []}
            demoTemp={demoTemp}
            setDemoTemp={setDemoTemp}
            onApplyDemo={applyDemoTemp}
            sunThreshold={sunThreshold}
            setSunThreshold={setSunThreshold}
            shadeThreshold={shadeThreshold}
            setShadeThreshold={setShadeThreshold}
            onApplyThresholds={applyThresholds}
            cautionTemp={cautionTemp}
            setCautionTemp={setCautionTemp}
            warningTemp={warningTemp}
            setWarningTemp={setWarningTemp}
            severeTemp={severeTemp}
            setSevereTemp={setSevereTemp}
            onApplyTempThresholds={applyTempThresholds}
            showDemoControls={showDemoControls}
            setShowDemoControls={setShowDemoControls}
          />
        )}

        <section className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(320px,0.75fr)]">
          <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl shadow-black/20">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3">
              <div>
                <h2 className="font-semibold text-white">현장 분석 영상</h2>
                <p className="text-xs text-slate-500">{currentSite?.name} · 사람 추적 · 안전모 · 위험구역 판정</p>
              </div>
              {cameraStreaming
                ? <span className="rounded-md bg-red-500/20 px-2.5 py-1 text-xs font-bold tracking-wider text-red-300">LIVE</span>
                : <span className="rounded-md bg-amber-500/15 px-2.5 py-1 text-xs font-bold tracking-wider text-amber-300">RECORDED</span>
              }
            </div>
            <ZoneEditor
              key={currentSite?.id}
              siteId={currentSite?.id}
              streamKey={currentSite?.id}
              streamSrc={`${API_BASE}/api/analysis/stream`}
              streamAlt={`${currentSite?.name} AI 영상 분석`}
              streamReady={streamReady}
              waitingMessage={summary?.analysis_message ?? '영상 스트림을 기다리고 있습니다.'}
              streamError={summary?.last_error}
              onStreamLoad={handleStreamLoad}
              onStreamError={handleStreamError}
              onRequestError={handleUnauthorized}
            />
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 px-5 py-3 text-xs text-slate-500">
              <span>프레임 #{summary?.frame_index ?? 0} · {cameraStreaming ? '브라우저 카메라 실시간 분석' : '녹화 영상 분석'}</span>
              <span>단일 카메라 추적 · {summary?.processing_fps ?? 0} FPS</span>
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
              )) : (
                <EmptyState text={
                  !connected ? '서버 연결 중...' :
                  !summary ? '분석 상태 불러오는 중...' :
                  summary.analysis_stage === 'stopped' ? '분석이 시작되지 않았습니다.' :
                  summary.analysis_stage === 'loading' ? 'AI 모델 준비 중...' :
                  (summary.analysis_stage === 'waiting_camera' || summary.analysis_stage === 'waiting_frame') ? '카메라 연결을 기다리는 중...' :
                  summary.analysis_stage === 'camera_disconnected' ? '카메라 연결이 종료되었습니다.' :
                  summary.analysis_stage === 'error' ? `분석 오류: ${summary.last_error ?? ''}` :
                  '현재 감지된 작업자가 없습니다.'
                } />
              )}
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
                  <th className="px-5 py-3 font-medium">발생 시각 (KST)</th>
                  <th className="px-5 py-3 font-medium">경고 유형</th>
                  <th className="px-5 py-3 font-medium">감지 ID</th>
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
                    <td className="px-5 py-3 tabular-nums text-cyan-300 text-xs">{event.track_id ? event.track_id.replace('person-', '#') : '-'}</td>
                    <td className="px-5 py-3">{event.zone_id ? `구역 ${event.zone_id}` : '일반구역'}</td>
                    <td className="px-5 py-3 tabular-nums">{Math.round(event.confidence * 100)}%</td>
                    <td className="px-5 py-3">
                      {event.resolved ? <span className="text-emerald-400">조치 완료</span> : (
                        <button onClick={() => resolveEvent(event.id)} className="rounded-md bg-red-500/15 px-2.5 py-1 text-xs text-red-300 hover:bg-red-500/25">조치 처리</button>
                      )}
                    </td>
                  </tr>
                )) : <tr><td colSpan="6" className="px-5 py-10 text-center text-slate-500">현재 현장에 저장된 안전 이벤트가 없습니다.</td></tr>}
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
        <span className="font-semibold text-white">추적 ID · {worker.track_id ?? worker.id}</span>
        <div className="flex items-center gap-1.5">
          {worker.shade_status === 'sun' && (
            <span className="rounded-full bg-orange-500/20 px-2 py-0.5 text-[10px] font-bold text-orange-300">양지</span>
          )}
          {worker.shade_status === 'shade' && (
            <span className="rounded-full bg-slate-600/40 px-2 py-0.5 text-[10px] font-bold text-slate-300">그늘</span>
          )}
          <span className="text-xs font-bold uppercase tracking-wider">{worker.level}</span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <HelmetPill state={worker.helmet_on} violation={worker.helmet_violation} />
      </div>
      <p className="mt-3 text-xs text-slate-400">위치 · {worker.zone}</p>
      <p className="mt-1 text-xs text-slate-400">객체 품질 · {Math.round((worker.image_quality ?? 0) * 100)}%</p>
      {worker.strong_rest_needed ? (
        <p className="mt-2 rounded-md bg-red-500/20 px-2 py-1.5 text-xs font-bold text-red-300">
          강력 휴식 권고 — 폭염구역 누적 20초 이상
        </p>
      ) : worker.rest_needed ? (
        <p className="mt-2 rounded-md bg-orange-500/15 px-2 py-1.5 text-xs font-semibold text-orange-300">
          휴식 권고 — 폭염구역 누적 10초 이상
        </p>
      ) : null}
      {worker.reasons?.length > 0 && <p className="mt-1 text-xs font-medium">{worker.reasons.join(' · ')}</p>}
      {worker.behavior_state && worker.behavior_state !== 'NORMAL' && (
        <p className={`mt-2 rounded-md px-2 py-1.5 text-xs font-semibold ${behaviorStyles[worker.behavior_state] ?? 'bg-slate-700/30 text-slate-300'}`}>
          {worker.behavior_state === 'FALL_STILL' || worker.behavior_state === 'FALL' ? '🚨' : '⚠'} {worker.behavior_label ?? worker.behavior_state}
        </p>
      )}
    </article>
  )
}

function HeatSection({
  heatStatus, workers,
  demoTemp, setDemoTemp, onApplyDemo,
  sunThreshold, setSunThreshold, shadeThreshold, setShadeThreshold, onApplyThresholds,
  cautionTemp, setCautionTemp, warningTemp, setWarningTemp, severeTemp, setSevereTemp, onApplyTempThresholds,
  showDemoControls, setShowDemoControls,
}) {
  const meta = heatLevelMeta[heatStatus.level]
  const restCount = workers.filter((w) => w.rest_needed).length
  const strongRestCount = workers.filter((w) => w.strong_rest_needed).length
  const inactive = !meta

  const borderBg = inactive
    ? 'border-slate-700 bg-slate-900/60'
    : `${meta.bg} ${meta.border}`

  return (
    <div className="mb-5 space-y-3">
      {heatStatus.level === 'severe' && (
        <div className="flex items-center gap-3 rounded-xl border border-red-500/50 bg-red-500/10 px-5 py-3 text-red-300">
          <span className="text-xl">⚠</span>
          <div>
            <p className="font-bold">옥외작업 중지 검토 권고</p>
            <p className="text-xs text-red-300/70">체감온도 {heatStatus.severe_temp ?? 38}°C 이상 — 옥외 작업자의 안전을 즉시 확인하세요.</p>
          </div>
        </div>
      )}

      <div className={`rounded-xl border px-5 py-4 ${borderBg}`}>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              {inactive
                ? <span className="text-sm font-bold text-slate-400">폭염 모니터링</span>
                : <span className={`text-sm font-bold ${meta.text}`}>{meta.label}</span>
              }
              {heatStatus.demo_mode && (
                <span className="rounded bg-violet-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-violet-300">DEMO</span>
              )}
              {heatStatus.stale && (
                <span className="rounded bg-slate-600/40 px-1.5 py-0.5 text-[10px] text-slate-400">기상 데이터 오래됨</span>
              )}
            </div>
            <p className={`mt-1 text-2xl font-bold tabular-nums ${inactive ? 'text-slate-400' : meta.text}`}>
              {heatStatus.apparent_temp !== null ? `${heatStatus.apparent_temp}°C` : '--'}
              <span className="ml-2 text-sm font-normal text-slate-500">체감온도</span>
            </p>
            {restCount > 0 && (
              <p className="mt-1 text-xs text-orange-300">
                휴식 권고 {restCount}명{strongRestCount > 0 ? ` · 강력 권고 ${strongRestCount}명` : ''}
              </p>
            )}
            <p className="mt-1 text-xs text-slate-600">
              * 본 정보는 위험 추정 보조 자료이며 건강 데이터를 저장하지 않습니다.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-400">
              체감온도 직접 설정 (°C)
              <div className="mt-1 flex items-center gap-1.5">
                <input
                  type="number"
                  step="0.1"
                  value={demoTemp}
                  onChange={(e) => setDemoTemp(e.target.value)}
                  placeholder="비워두면 해제"
                  className="w-32 rounded border border-slate-600 bg-slate-950 px-2 py-1 text-sm text-white outline-none focus:border-violet-500"
                />
                <button
                  onClick={onApplyDemo}
                  className="rounded-lg bg-violet-500 px-3 py-1.5 text-xs font-semibold text-white hover:bg-violet-400"
                >
                  적용
                </button>
              </div>
            </label>

            <button
              onClick={() => setShowDemoControls((v) => !v)}
              className="self-end rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs text-slate-500 hover:border-slate-500 hover:text-slate-300"
            >
              임계값
            </button>
          </div>
        </div>

        {showDemoControls && (
          <div className="mt-4 space-y-4 border-t border-slate-700/50 pt-4">
            {/* 폭염 단계 온도 설정 */}
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-400">폭염 단계 온도 (°C)</p>
              <div className="flex flex-wrap items-end gap-3">
                <label className="block text-xs text-slate-500">
                  주의
                  <input
                    type="number" step="0.5" min="20" max="50"
                    value={cautionTemp}
                    onChange={(e) => setCautionTemp(Number(e.target.value))}
                    className="mt-1 block w-20 rounded border border-amber-700/50 bg-slate-950 px-2 py-1 text-sm text-amber-200 outline-none focus:border-amber-500"
                  />
                </label>
                <label className="block text-xs text-slate-500">
                  경보
                  <input
                    type="number" step="0.5" min="20" max="50"
                    value={warningTemp}
                    onChange={(e) => setWarningTemp(Number(e.target.value))}
                    className="mt-1 block w-20 rounded border border-orange-700/50 bg-slate-950 px-2 py-1 text-sm text-orange-200 outline-none focus:border-orange-500"
                  />
                </label>
                <label className="block text-xs text-slate-500">
                  심각 (무조건 폭염)
                  <input
                    type="number" step="0.5" min="20" max="50"
                    value={severeTemp}
                    onChange={(e) => setSevereTemp(Number(e.target.value))}
                    className="mt-1 block w-20 rounded border border-red-700/50 bg-slate-950 px-2 py-1 text-sm text-red-200 outline-none focus:border-red-500"
                  />
                </label>
                <button
                  onClick={onApplyTempThresholds}
                  className="self-end rounded-lg bg-amber-600/80 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-500"
                >
                  온도 적용
                </button>
              </div>
            </div>

            {/* 양지/그늘 감지 임계값 */}
            <div>
              <p className="mb-2 text-xs font-semibold text-slate-400">양지·그늘 감지 임계값 (밝기 비율)</p>
              <div className="flex flex-wrap items-end gap-3">
                <label className="block text-xs text-slate-500">
                  양지 기준
                  <input
                    type="number" step="0.01" min="0" max="2"
                    value={sunThreshold}
                    onChange={(e) => setSunThreshold(Number(e.target.value))}
                    className="mt-1 block w-24 rounded border border-slate-600 bg-slate-950 px-2 py-1 text-sm text-white outline-none focus:border-orange-500"
                  />
                </label>
                <label className="block text-xs text-slate-500">
                  그늘 기준
                  <input
                    type="number" step="0.01" min="0.1" max="0.99"
                    value={shadeThreshold}
                    onChange={(e) => setShadeThreshold(Number(e.target.value))}
                    className="mt-1 block w-24 rounded border border-slate-600 bg-slate-950 px-2 py-1 text-sm text-white outline-none focus:border-slate-500"
                  />
                </label>
                <button
                  onClick={onApplyThresholds}
                  className="self-end rounded-lg border border-slate-600 px-3 py-1.5 text-xs text-slate-300 hover:border-orange-500 hover:text-orange-300"
                >
                  밝기 적용
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function StatusPill({ label, ok }) {
  return <span className={`rounded-md px-2 py-1.5 ${ok ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/15 text-red-300'}`}>{label} {ok ? '정상' : '미확인'}</span>
}

function HelmetPill({ state, violation }) {
  if (state === true) return <StatusPill label="안전모" ok />
  if (state === false && violation) return <span className="rounded-md bg-red-500/15 px-2 py-1.5 text-red-300">안전모 미착용</span>
  if (state === false) return <span className="rounded-md bg-slate-700/40 px-2 py-1.5 text-slate-300">안전모 미착용 · 일반구역 허용</span>
  return <span className="rounded-md bg-slate-700/40 px-2 py-1.5 text-slate-400">안전모 판정 불가</span>
}

function EmptyState({ text }) {
  return <div className="rounded-xl border border-dashed border-slate-700 px-4 py-10 text-center text-sm text-slate-500">{text}</div>
}

function formatDate(value) {
  if (!value) return '-'
  const hasTimeZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value)
  const timestamp = hasTimeZone ? value : `${value}+09:00`
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(timestamp))
}
