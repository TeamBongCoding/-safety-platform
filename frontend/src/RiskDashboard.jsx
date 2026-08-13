import { useCallback, useEffect, useState } from 'react'
import { fetchRiskConfig, fetchRiskOverview, generateRiskReport, fetchLatestReport, fetchEpisodes, resolveEpisode, fetchDocuments, deleteDocument, uploadDocument } from './api'

const RISK_LEVEL_STYLE = {
  low:      { border: 'border-emerald-500/30', bg: 'bg-emerald-500/10', text: 'text-emerald-300', badge: 'bg-emerald-500/20 text-emerald-200' },
  medium:   { border: 'border-amber-500/30',   bg: 'bg-amber-500/10',   text: 'text-amber-300',   badge: 'bg-amber-500/20 text-amber-200' },
  high:     { border: 'border-orange-500/30',  bg: 'bg-orange-500/10',  text: 'text-orange-300',  badge: 'bg-orange-500/20 text-orange-200' },
  critical: { border: 'border-red-500/30',     bg: 'bg-red-500/10',     text: 'text-red-300',     badge: 'bg-red-500/20 text-red-200' },
}

const RISK_LABEL = { low: '낮음', medium: '보통', high: '높음', critical: '위험' }

const EVENT_LABELS = {
  no_helmet: '안전모 미착용', zone_intrusion: '위험구역 침입', fall: '쓰러짐',
  fall_still: '쓰러짐+정지', fall_risk_entry: '추락위험 진입',
  heavy_equipment_entry: '중장비 작업반경', stagger: '휘청거림',
  sudden_sit: '주저앉음', heat_fall: '폭염 쓰러짐', heat_stagger: '폭염 휘청거림',
}
const DEFAULT_RISK_CONFIG = {
  mode: 'production',
  refresh_seconds: 60,
  options: [{ value: '24h', label: '24시간' }, { value: '7d', label: '7일' }],
}


function eventLabel(type) { return EVENT_LABELS[type] || type }

function RiskBadge({ level }) {
  const s = RISK_LEVEL_STYLE[level] || RISK_LEVEL_STYLE.low
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${s.badge}`}>
      {RISK_LABEL[level] || level}
    </span>
  )
}

function ScoreBar({ score, level }) {
  const s = RISK_LEVEL_STYLE[level] || RISK_LEVEL_STYLE.low
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`h-full rounded-full transition-all duration-500 ${s.bg.replace('/10', '/80')}`}
          style={{ width: `${Math.min(100, score)}%` }}
        />
      </div>
      <span className={`w-10 text-right text-sm font-bold tabular-nums ${s.text}`}>{score.toFixed(0)}</span>
    </div>
  )
}

// ── Overview Tab ─────────────────────────────────────────────────────────────

function OverviewTab({ onError, riskConfig }) {
  const [horizon, setHorizon] = useState('24h')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setLoading(true)
    try {
      const result = await fetchRiskOverview(horizon)
      setData(result)
    } catch (err) {
      if (err.status !== 404) onError(err.message)
      if (showLoading) setData(null)
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [horizon, onError])

  useEffect(() => {
    load()
    const interval = window.setInterval(() => load(false), Math.max(1, riskConfig.refresh_seconds) * 1000)
    return () => window.clearInterval(interval)
  }, [load, riskConfig.refresh_seconds])

  const overallStyle = RISK_LEVEL_STYLE[data?.overall_risk_level] || RISK_LEVEL_STYLE.low

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-white">위험 추세 현황</h2>
            {riskConfig.mode === 'demo' && (
              <span className="rounded-full bg-fuchsia-500/20 px-2 py-0.5 text-xs font-semibold text-fuchsia-200">데모 모드</span>
            )}
          </div>
          {riskConfig.mode === 'demo' && <p className="mt-0.5 text-xs text-slate-500">{riskConfig.refresh_seconds}초마다 자동 갱신</p>}
        </div>
        <div className="flex gap-1 rounded-lg border border-slate-700 p-0.5">
          {riskConfig.options.map((option) => (
            <button
              key={option.value}
              onClick={() => setHorizon(option.value)}
              className={`rounded-md px-3 py-1 text-sm font-medium transition ${horizon === option.value ? 'bg-cyan-500 text-slate-950' : 'text-slate-400 hover:text-white'}`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="text-sm text-slate-500">분석 중...</p>}

      {!loading && data && (
        <>
          <div className={`rounded-xl border p-4 ${overallStyle.border} ${overallStyle.bg}`}>
            <p className="text-xs font-semibold uppercase tracking-widest text-slate-400">전체 위험도</p>
            <div className="mt-2 flex items-center gap-3">
              <span className={`text-4xl font-bold tabular-nums ${overallStyle.text}`}>{data.overall_risk_score.toFixed(0)}</span>
              <span className="text-slate-500">/100</span>
              <RiskBadge level={data.overall_risk_level} />
            </div>
            <ScoreBar score={data.overall_risk_score} level={data.overall_risk_level} />
          </div>

          <div className="space-y-2">
            {data.results.map((r) => (
              <div key={r.event_type} className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-slate-200">{eventLabel(r.event_type)}</span>
                    <RiskBadge level={r.risk_level} />
                  </div>
                  <ScoreBar score={r.risk_score} level={r.risk_level} />
                </div>
                <div className="hidden shrink-0 text-right sm:block">
                  <p className="text-xs text-slate-500">변화율</p>
                  <p className={`text-sm font-bold tabular-nums ${r.change_percent > 0 ? 'text-red-300' : 'text-emerald-300'}`}>
                    {r.change_percent > 0 ? '+' : ''}{r.change_percent.toFixed(0)}%
                  </p>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {!loading && !data && (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-8 text-center">
          <p className="text-slate-400">아직 영상 분석 데이터가 없습니다.</p>
          <p className="mt-1 text-xs text-slate-600">영상 분석을 실행하면 위험 추세가 자동으로 계산됩니다.</p>
        </div>
      )}
    </div>
  )
}

// ── Report Tab ────────────────────────────────────────────────────────────────

function ReportTab({ onError, riskConfig }) {
  const [horizon, setHorizon] = useState('7d')
  const [eventType, setEventType] = useState('no_helmet')
  const [report, setReport] = useState(null)
  const [generating, setGenerating] = useState(false)

  const loadLatest = useCallback(async () => {
    try {
      const r = await fetchLatestReport(horizon, eventType)
      setReport(r)
    } catch {
      setReport(null)
    }
  }, [horizon, eventType])

  useEffect(() => { loadLatest() }, [loadLatest])

  const generate = async () => {
    setGenerating(true)
    try {
      const r = await generateRiskReport(horizon, eventType)
      setReport(r)
    } catch (err) {
      onError(err.message)
    } finally {
      setGenerating(false)
    }
  }

  const llm = report?.llm_report

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold text-white">AI 위험 보고서</h2>
        <div className="flex gap-1 rounded-lg border border-slate-700 p-0.5">
          {riskConfig.options.map((option) => (
            <button key={option.value} onClick={() => setHorizon(option.value)} className={`rounded-md px-2.5 py-1 text-xs font-medium transition ${horizon === option.value ? 'bg-cyan-500 text-slate-950' : 'text-slate-400 hover:text-white'}`}>
              {option.label}
            </button>
          ))}
        </div>
        <select
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-900 px-2.5 py-1.5 text-sm text-white outline-none focus:border-cyan-500"
        >
          {Object.entries(EVENT_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <button
          onClick={generate}
          disabled={generating}
          className="ml-auto rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:cursor-wait disabled:opacity-50"
        >
          {generating ? '생성 중...' : '보고서 생성'}
        </button>
      </div>

      {report ? (
        <div className="space-y-4">
          <div className={`rounded-xl border p-4 ${(RISK_LEVEL_STYLE[report.risk_level] || RISK_LEVEL_STYLE.low).border} ${(RISK_LEVEL_STYLE[report.risk_level] || RISK_LEVEL_STYLE.low).bg}`}>
            <div className="flex items-center gap-3">
              <span className="text-sm text-slate-400">{eventLabel(report.event_type)}</span>
              <RiskBadge level={report.risk_level} />
              <span className="ml-auto text-xs text-slate-500">{new Date(report.generated_at).toLocaleString('ko-KR')}</span>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <span className={`text-3xl font-bold tabular-nums ${(RISK_LEVEL_STYLE[report.risk_level] || RISK_LEVEL_STYLE.low).text}`}>{report.risk_score.toFixed(0)}</span>
              <span className="text-slate-500">/100</span>
              {report.change_percent != null && (
                <span className={`ml-2 text-sm font-semibold ${report.change_percent > 0 ? 'text-red-300' : 'text-emerald-300'}`}>
                  {report.change_percent > 0 ? '+' : ''}{report.change_percent.toFixed(0)}%
                </span>
              )}
            </div>
          </div>

          {llm ? (
            <>
              <Section title="요약">
                <p className="text-sm leading-relaxed text-slate-300">{llm.summary}</p>
              </Section>

              {llm.evidence?.length > 0 && (
                <Section title="근거 지표">
                  <ul className="space-y-1.5">
                    {llm.evidence.map((e, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm text-slate-300">
                        <span className="mt-0.5 text-slate-500">•</span>
                        <span><span className="font-medium text-slate-200">{e.metric}</span> — {e.description}</span>
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {llm.recommendations?.length > 0 && (
                <Section title="우선 조치 사항">
                  <ol className="space-y-2">
                    {llm.recommendations.map((rec, i) => (
                      <li key={i} className="flex gap-3 text-sm">
                        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-cyan-500/20 text-xs font-bold text-cyan-300">{rec.priority}</span>
                        <div>
                          <p className="font-medium text-slate-200">{rec.action}</p>
                          <p className="text-slate-500">{rec.reason}</p>
                        </div>
                      </li>
                    ))}
                  </ol>
                </Section>
              )}

              {llm.citations?.length > 0 && (
                <Section title="참고 문서">
                  <ul className="space-y-1">
                    {llm.citations.map((c, i) => (
                      <li key={i} className="text-xs text-slate-400">
                        [{c.chunk_id}] {c.title}{c.section ? ` — ${c.section}` : ''}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}

              {llm.limitations?.length > 0 && (
                <div className="rounded-lg border border-slate-700/50 bg-slate-900/50 px-4 py-3">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-500">한계 및 주의</p>
                  <ul className="space-y-0.5">
                    {llm.limitations.map((l, i) => (
                      <li key={i} className="text-xs text-slate-500">{l}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-center text-sm text-slate-500">
              LLM이 비활성화되어 있습니다. Risk Engine 결과만 표시됩니다.
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-8 text-center">
          <p className="text-slate-400">생성된 보고서가 없습니다.</p>
          <p className="mt-1 text-xs text-slate-600">위 버튼으로 보고서를 생성하세요.</p>
        </div>
      )}
    </div>
  )
}

// ── Episodes Tab ──────────────────────────────────────────────────────────────

function EpisodesTab({ onError }) {
  const [episodes, setEpisodes] = useState([])
  const [loading, setLoading] = useState(false)
  const [showResolved, setShowResolved] = useState(false)
  const [note, setNote] = useState('')
  const [resolvingId, setResolvingId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchEpisodes({ resolved: showResolved, limit: 50 })
      setEpisodes(data || [])
    } catch (err) {
      onError(err.message)
    } finally {
      setLoading(false)
    }
  }, [showResolved, onError])

  useEffect(() => { load() }, [load])

  const doResolve = async (id) => {
    try {
      await resolveEpisode(id, note)
      setResolvingId(null)
      setNote('')
      load()
    } catch (err) {
      onError(err.message)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">위험 사건 목록</h2>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-400">
          <input type="checkbox" checked={showResolved} onChange={(e) => setShowResolved(e.target.checked)} className="accent-cyan-500" />
          해결된 사건 포함
        </label>
      </div>

      {loading && <p className="text-sm text-slate-500">불러오는 중...</p>}

      {!loading && episodes.length === 0 && (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-8 text-center">
          <p className="text-slate-400">사건이 없습니다.</p>
        </div>
      )}

      <div className="space-y-2">
        {episodes.map((ep) => {
          const s = RISK_LEVEL_STYLE[ep.severity === 'high' ? 'high' : ep.severity === 'medium' ? 'medium' : 'low']
          return (
            <div key={ep.id} className={`rounded-xl border ${ep.resolved ? 'border-slate-700/50 opacity-60' : s.border} bg-slate-900 p-4`}>
              <div className="flex flex-wrap items-start gap-3">
                <div className="flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-200">{eventLabel(ep.event_type)}</span>
                    <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${s.badge}`}>{ep.severity}</span>
                    {ep.resolved && <span className="rounded-full bg-slate-700 px-2 py-0.5 text-xs text-slate-400">해결됨</span>}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {new Date(ep.started_at).toLocaleString('ko-KR')}
                    {ep.duration_sec > 0 && ` · ${ep.duration_sec.toFixed(0)}초`}
                    {ep.observation_count > 1 && ` · ${ep.observation_count}회 관측`}
                    {ep.track_id && ` · 추적 ${ep.track_id}`}
                  </p>
                  {ep.resolution_note && (
                    <p className="mt-1 text-xs text-slate-600">조치: {ep.resolution_note}</p>
                  )}
                </div>
                {!ep.resolved && (
                  resolvingId === ep.id ? (
                    <div className="flex items-center gap-2">
                      <input
                        value={note}
                        onChange={(e) => setNote(e.target.value)}
                        placeholder="조치 내용 (선택)"
                        className="rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-white outline-none focus:border-cyan-500"
                      />
                      <button onClick={() => doResolve(ep.id)} className="rounded-lg bg-emerald-500/20 px-3 py-1.5 text-sm text-emerald-300 hover:bg-emerald-500/30">확인</button>
                      <button onClick={() => { setResolvingId(null); setNote('') }} className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400">취소</button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setResolvingId(ep.id)}
                      className="shrink-0 rounded-lg border border-emerald-500/30 px-3 py-1.5 text-xs text-emerald-300 hover:bg-emerald-500/10"
                    >
                      조치 완료
                    </button>
                  )
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Knowledge Tab ─────────────────────────────────────────────────────────────

function KnowledgeTab({ onError }) {
  const [docs, setDocs] = useState([])
  const [uploading, setUploading] = useState(false)
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')

  const load = useCallback(async () => {
    try {
      const data = await fetchDocuments()
      setDocs(data || [])
    } catch (err) {
      onError(err.message)
    }
  }, [onError])

  useEffect(() => { load() }, [load])

  const upload = async (e) => {
    e.preventDefault()
    if (!file || !title.trim()) return
    const fd = new FormData()
    fd.append('file', file)
    fd.append('title', title.trim())
    setUploading(true)
    try {
      await uploadDocument(fd)
      setFile(null)
      setTitle('')
      load()
    } catch (err) {
      onError(err.message)
    } finally {
      setUploading(false)
    }
  }

  const del = async (id) => {
    try {
      await deleteDocument(id)
      setDocs((prev) => prev.filter((d) => d.id !== id))
    } catch (err) {
      onError(err.message)
    }
  }

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-white">안전 지식 문서</h2>

      <form onSubmit={upload} className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">문서 업로드 (PDF / TXT)</p>
        <div className="flex flex-wrap gap-3">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="문서 제목"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
          />
          <label className="flex cursor-pointer items-center rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-400 hover:border-cyan-500">
            {file ? file.name : '파일 선택'}
            <input type="file" accept=".pdf,.txt" className="sr-only" onChange={(e) => setFile(e.target.files[0])} />
          </label>
          <button
            type="submit"
            disabled={!file || !title.trim() || uploading}
            className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-40"
          >
            {uploading ? '업로드 중...' : '업로드'}
          </button>
        </div>
      </form>

      {docs.length === 0 ? (
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-8 text-center">
          <p className="text-slate-400">등록된 문서가 없습니다.</p>
          <p className="mt-1 text-xs text-slate-600">안전 매뉴얼을 업로드하면 AI 보고서 생성 시 참고 자료로 활용됩니다.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
            <div key={doc.id} className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-slate-200">{doc.title}</p>
                <p className="text-xs text-slate-500">{doc.source || '—'} · v{doc.version} · {doc.chunk_count ?? 0}청크</p>
              </div>
              <button
                onClick={() => del(doc.id)}
                className="shrink-0 rounded-lg border border-red-500/30 px-2.5 py-1.5 text-xs text-red-400 hover:bg-red-500/10"
              >
                삭제
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

function Section({ title, children }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
      <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">{title}</p>
      {children}
    </div>
  )
}

// ── Main export ───────────────────────────────────────────────────────────────

const TABS = [
  { id: 'overview', label: '위험 현황' },
  { id: 'report',   label: 'AI 보고서' },
  { id: 'episodes', label: '사건 목록' },
  { id: 'knowledge',label: '지식 문서' },
]

export default function RiskDashboard({ onBack }) {
  const [tab, setTab] = useState('overview')
  const [error, setError] = useState('')
  const [riskConfig, setRiskConfig] = useState(DEFAULT_RISK_CONFIG)

  useEffect(() => {
    fetchRiskConfig()
      .then(setRiskConfig)
      .catch((err) => setError(err.message))
  }, [])

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={onBack}
          className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:border-slate-500 hover:text-white"
        >
          ← 관제 화면
        </button>
        <h1 className="text-xl font-bold text-white">위험 추세 & 분석</h1>
      </div>

      {error && (
        <div role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300">
          {error}
          <button onClick={() => setError('')} className="ml-3 text-red-400 hover:text-red-200">✕</button>
        </div>
      )}

      <div className="flex gap-1 rounded-xl border border-slate-800 bg-slate-900/60 p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 rounded-lg py-2 text-sm font-medium transition ${tab === t.id ? 'bg-slate-800 text-white shadow' : 'text-slate-500 hover:text-slate-300'}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview'  && <OverviewTab  onError={setError} riskConfig={riskConfig} />}
      {tab === 'report'    && <ReportTab    onError={setError} riskConfig={riskConfig} />}
      {tab === 'episodes'  && <EpisodesTab  onError={setError} />}
      {tab === 'knowledge' && <KnowledgeTab onError={setError} />}
    </div>
  )
}
