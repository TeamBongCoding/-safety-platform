import { useEffect, useMemo, useState } from 'react'
import { api } from './api'

const rankingTypes = [
  ['companies', '회사별'],
  ['sites', '현장별'],
  ['zones', '구역별'],
]

export default function RankingDashboard({ onBack, currentCompany }) {
  const [rankingType, setRankingType] = useState('companies')
  const [rankings, setRankings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const loadRankings = async () => {
    setLoading(true)
    setError('')
    try {
      setRankings(await api('/api/rankings/today'))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    api('/api/rankings/today')
      .then((data) => {
        if (active) setRankings(data)
      })
      .catch((requestError) => {
        if (active) setError(requestError.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [])

  const rows = useMemo(() => rankings?.[rankingType] ?? [], [rankingType, rankings])

  return (
    <section className="space-y-5">
      <header className="rounded-2xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/10 via-slate-900 to-cyan-500/5 p-5 sm:p-7">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold tracking-[0.22em] text-emerald-400">DAILY SAFETY RANKING</p>
            <h1 className="mt-2 text-2xl font-bold text-white sm:text-3xl">오늘의 안전 순위</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
              오늘 위험구역 내부에서 발생한 안전모 미착용 경고가 적은 순서입니다. 경고 수가 같으면 공동 순위로 표시됩니다.
            </p>
            {rankings?.date && <p className="mt-3 text-xs text-slate-500">집계일 {formatDate(rankings.date)}</p>}
          </div>
          <div className="flex gap-2">
            <button onClick={loadRankings} disabled={loading} className="rounded-lg border border-emerald-500/30 px-3 py-2 text-sm text-emerald-300 hover:bg-emerald-500/10 disabled:opacity-50">새로고침</button>
            {onBack && <button onClick={onBack} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:border-cyan-500 hover:text-cyan-300">관제 화면으로</button>}
          </div>
        </div>
      </header>

      <nav className="flex gap-2 overflow-x-auto" aria-label="안전 순위 분류">
        {rankingTypes.map(([value, label]) => (
          <button
            key={value}
            onClick={() => setRankingType(value)}
            className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-semibold transition ${rankingType === value ? 'bg-emerald-500 text-slate-950' : 'border border-slate-700 bg-slate-900 text-slate-400 hover:text-white'}`}
          >
            {label} 순위
          </button>
        ))}
      </nav>

      <p className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-4 py-3 text-xs text-amber-200/80">
        모든 순위는 위험구역 내부의 안전모 미착용 경고만 집계하며, 경고가 적을수록 높은 순위입니다.
      </p>

      <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
        {error ? (
          <div role="alert" className="p-8 text-center"><p className="text-red-300">{error}</p><button onClick={loadRankings} className="mt-4 rounded-lg border border-red-500/30 px-3 py-2 text-sm text-red-300">다시 시도</button></div>
        ) : loading ? (
          <div className="grid min-h-[280px] place-items-center"><div className="text-center"><div className="mx-auto mb-3 h-9 w-9 animate-spin rounded-full border-2 border-slate-700 border-t-emerald-400" /><p className="text-sm text-slate-500">오늘 순위를 집계하는 중...</p></div></div>
        ) : (
          <RankingTable rows={rows} type={rankingType} currentCompany={currentCompany} />
        )}
      </div>
    </section>
  )
}

function RankingTable({ rows, type, currentCompany }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] text-left text-sm">
        <thead className="bg-slate-950/60 text-xs uppercase tracking-wider text-slate-500">
          <tr>
            <th className="w-24 px-5 py-4">순위</th>
            <th className="px-5 py-4">대상</th>
            <th className="px-5 py-4">소속</th>
            <th className="w-36 px-5 py-4 text-right">오늘 경고</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {rows.length ? rows.map((row) => {
            const key = row.company_name + (row.site_id ?? '') + (row.zone_id ?? '')
            const isMine = currentCompany && row.company_name === currentCompany
            return (
              <tr key={key} className={isMine ? 'bg-cyan-500/5 text-slate-200' : 'text-slate-300 hover:bg-slate-800/40'}>
                <td className="px-5 py-4"><RankBadge rank={row.rank} /></td>
                <td className="px-5 py-4">
                  <p className="font-semibold text-white">{rowTitle(row, type)}</p>
                  {isMine && <span className="mt-1 inline-block rounded bg-cyan-500/10 px-2 py-0.5 text-[11px] font-medium text-cyan-300">우리 회사</span>}
                </td>
                <td className="px-5 py-4 text-slate-400">{rowOwner(row, type)}</td>
                <td className="px-5 py-4 text-right"><WarningCount count={row.warning_count} /></td>
              </tr>
            )
          }) : <tr><td colSpan="4" className="px-5 py-12 text-center text-slate-500">순위를 표시할 등록 데이터가 없습니다.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function RankBadge({ rank }) {
  const style = rank === 1
    ? 'border-amber-400/30 bg-amber-400/10 text-amber-300'
    : rank === 2
      ? 'border-slate-400/30 bg-slate-400/10 text-slate-300'
      : rank === 3
        ? 'border-orange-500/30 bg-orange-500/10 text-orange-300'
        : 'border-slate-700 bg-slate-800 text-slate-400'
  return <span className={`inline-flex min-w-12 justify-center rounded-full border px-2.5 py-1 text-xs font-bold ${style}`}>{rank}위</span>
}

function WarningCount({ count }) {
  if (count === 0) return <span className="rounded-full bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-300">0건 · 안전</span>
  return <span className="font-semibold tabular-nums text-red-300">{count}건</span>
}

function rowTitle(row, type) {
  if (type === 'companies') return row.company_name
  if (type === 'sites') return row.site_name
  return row.zone_name
}

function rowOwner(row, type) {
  if (type === 'companies') return `${row.site_count}개 현장`
  if (type === 'sites') return row.company_name
  return `${row.company_name} · ${row.site_name}`
}

function formatDate(value) {
  return new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' })
    .format(new Date(`${value}T00:00:00`))
}
