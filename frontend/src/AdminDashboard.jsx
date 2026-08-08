import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import RankingDashboard from './RankingDashboard'

const tabs = [
  ['overview', '전체 현황'],
  ['accounts', '계정 관리'],
  ['events', '전체 위험 기록'],
  ['rankings', '오늘 안전 순위'],
  ['audit', '감사 로그'],
]

const actionLabels = {
  account_suspended: '계정 정지',
  account_activated: '계정 복구',
  account_deleted: '계정 영구 삭제',
  events_deleted_today: '오늘 이벤트 전체 삭제',
  events_deleted_all: '모든 이벤트 전체 삭제',
}

export default function AdminDashboard({ session, setSession }) {
  const [tab, setTab] = useState('overview')
  const [overview, setOverview] = useState(null)
  const [users, setUsers] = useState([])
  const [events, setEvents] = useState([])
  const [auditLogs, setAuditLogs] = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loading, setLoading] = useState(true)
  const [deletingEventScope, setDeletingEventScope] = useState('')

  const handleError = useCallback((requestError) => {
    if (requestError.status === 401) setSession(null)
    else setError(requestError.message)
  }, [setSession])

  const loadOverview = useCallback(async () => {
    try {
      setOverview(await api('/api/admin/overview'))
    } catch (requestError) {
      handleError(requestError)
    }
  }, [handleError])

  const loadUsers = useCallback(async () => {
    try {
      setUsers(await api('/api/admin/users'))
    } catch (requestError) {
      handleError(requestError)
    }
  }, [handleError])

  useEffect(() => {
    let active = true
    Promise.all([api('/api/admin/overview'), api('/api/admin/users')])
      .then(([nextOverview, nextUsers]) => {
        if (!active) return
        setOverview(nextOverview)
        setUsers(nextUsers)
      })
      .catch((requestError) => {
        if (active) handleError(requestError)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => { active = false }
  }, [handleError])

  const selectTab = async (nextTab) => {
    setTab(nextTab)
    setError('')
    setNotice('')
    try {
      if (nextTab === 'events') setEvents(await api('/api/admin/events?limit=200'))
      if (nextTab === 'audit') setAuditLogs(await api('/api/admin/audit-logs?limit=200'))
      if (nextTab === 'accounts') await loadUsers()
      if (nextTab === 'overview') await loadOverview()
    } catch (requestError) {
      handleError(requestError)
    }
  }

  const openUser = async (userId) => {
    try {
      setSelectedUser(await api(`/api/admin/users/${userId}`))
    } catch (requestError) {
      handleError(requestError)
    }
  }

  const refreshAfterAction = async (userId) => {
    await Promise.all([loadOverview(), loadUsers()])
    if (userId) await openUser(userId)
  }

  const changeStatus = async (user, action) => {
    setError('')
    try {
      await api(`/api/admin/users/${user.id}/${action}`, { method: 'POST' })
      await refreshAfterAction(user.id)
    } catch (requestError) {
      handleError(requestError)
    }
  }

  const deleteAccount = async (user, confirmationEmail) => {
    setError('')
    try {
      await api(`/api/admin/users/${user.id}`, {
        method: 'DELETE',
        body: JSON.stringify({ email: confirmationEmail }),
      })
      setSelectedUser(null)
      await refreshAfterAction(null)
    } catch (requestError) {
      handleError(requestError)
      throw requestError
    }
  }

  const deleteEvents = async (scope) => {
    const message = scope === 'today'
      ? '오늘 발생한 모든 계정의 이벤트 기록을 삭제할까요? 삭제한 기록은 복구할 수 없습니다.'
      : '모든 계정의 전체 이벤트 기록을 영구 삭제할까요? 이 작업은 복구할 수 없습니다.'
    if (!window.confirm(message)) return

    setError('')
    setNotice('')
    setDeletingEventScope(scope)
    try {
      const result = await api(`/api/admin/events?scope=${scope}`, { method: 'DELETE' })
      setEvents(await api('/api/admin/events?limit=200'))
      await loadOverview()
      setNotice(`${result.deleted_count}건의 이벤트 기록을 삭제했습니다.`)
    } catch (requestError) {
      handleError(requestError)
    } finally {
      setDeletingEventScope('')
    }
  }

  const logout = async () => {
    try {
      await api('/api/auth/logout', { method: 'POST' })
    } finally {
      setSession(null)
    }
  }

  const filteredUsers = useMemo(() => {
    const term = search.trim().toLowerCase()
    return users.filter((user) => {
      const matchesTerm = !term || [user.email, user.company_name, user.manager_name]
        .some((value) => value?.toLowerCase().includes(term))
      const matchesStatus = statusFilter === 'all' || user.status === statusFilter
      return matchesTerm && matchesStatus
    })
  }, [users, search, statusFilter])

  return (
    <main className="min-h-screen bg-[#07111f] text-slate-100">
      <header className="border-b border-slate-800 bg-slate-950/70">
        <div className="mx-auto flex max-w-[1600px] flex-col gap-4 px-4 py-5 sm:px-6 lg:flex-row lg:items-center lg:justify-between lg:px-8">
          <div>
            <p className="text-xs font-semibold tracking-[0.22em] text-violet-400">PLATFORM ADMINISTRATION</p>
            <h1 className="mt-1 text-2xl font-bold text-white">서버 관리자 콘솔</h1>
            <p className="mt-1 text-sm text-slate-500">{session.user.email} · 모든 계정과 현장을 총괄합니다.</p>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full border border-violet-500/30 bg-violet-500/10 px-3 py-1.5 text-xs font-semibold text-violet-300">SERVER ADMIN</span>
            <button onClick={logout} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:border-red-500/50 hover:text-red-300">로그아웃</button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
        <nav className="mb-6 flex gap-2 overflow-x-auto border-b border-slate-800 pb-3" aria-label="관리자 메뉴">
          {tabs.map(([value, label]) => (
            <button key={value} onClick={() => selectTab(value)} className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium transition ${tab === value ? 'bg-violet-500 text-white' : 'text-slate-400 hover:bg-slate-800 hover:text-white'}`}>{label}</button>
          ))}
        </nav>

        {error && <div role="alert" className="mb-5 flex items-center justify-between rounded-xl border border-red-500/30 bg-red-500/10 px-4 py-3 text-sm text-red-300"><span>{error}</span><button onClick={() => setError('')}>닫기</button></div>}
        {notice && <div role="status" className="mb-5 flex items-center justify-between rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300"><span>{notice}</span><button onClick={() => setNotice('')}>닫기</button></div>}

        {loading ? <AdminLoading /> : (
          <>
            {tab === 'overview' && <Overview overview={overview} users={users} onOpenUser={openUser} />}
            {tab === 'accounts' && (
              <Accounts
                users={filteredUsers}
                search={search}
                setSearch={setSearch}
                statusFilter={statusFilter}
                setStatusFilter={setStatusFilter}
                onOpenUser={openUser}
              />
            )}
            {tab === 'events' && (
              <AllEvents
                events={events}
                deletingScope={deletingEventScope}
                onDeleteToday={() => deleteEvents('today')}
                onDeleteAll={() => deleteEvents('all')}
              />
            )}
            {tab === 'rankings' && <RankingDashboard />}
            {tab === 'audit' && <AuditLogs logs={auditLogs} />}
          </>
        )}
      </div>

      {selectedUser && (
        <UserDetail
          user={selectedUser}
          currentAdminId={session.user.id}
          onClose={() => setSelectedUser(null)}
          onChangeStatus={changeStatus}
          onDelete={deleteAccount}
        />
      )}
    </main>
  )
}

function Overview({ overview, users, onOpenUser }) {
  if (!overview) return <AdminLoading />
  const cards = [
    ['전체 계정', overview.account_count, '개', 'cyan'],
    ['전체 현장', overview.site_count, '곳', 'violet'],
    ['등록 작업자', overview.worker_count, '명', 'emerald'],
    ['등록 카메라', overview.camera_count, '대', 'blue'],
    ['금일 위험', overview.events_today, '건', 'amber'],
    ['미조치 위험', overview.unresolved_count, '건', 'red'],
  ]
  return (
    <div className="space-y-6">
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        {cards.map(([label, value, unit, tone]) => <AdminMetric key={label} label={label} value={value} unit={unit} tone={tone} />)}
      </section>

      <section className="grid gap-5 xl:grid-cols-[1.5fr_0.7fr]">
        <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
          <SectionHeader title="최근 가입 계정" description="계정을 선택하면 현장과 데이터 사용량을 확인할 수 있습니다." />
          <AccountTable users={users.slice(0, 8)} onOpenUser={onOpenUser} />
        </div>
        <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="font-semibold text-white">시스템 상태</h2>
          <div className="mt-5 space-y-4">
            <SystemStatus label="FastAPI 서버" value="정상" ok />
            <SystemStatus label="데이터베이스" value={overview.database} ok />
            <SystemStatus label="활성 계정" value={`${overview.active_account_count}개`} ok />
            <SystemStatus label="누적 위험 기록" value={`${overview.event_count}건`} ok />
          </div>
        </div>
      </section>
    </div>
  )
}

function Accounts({ users, search, setSearch, statusFilter, setStatusFilter, onOpenUser }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
      <div className="flex flex-col gap-3 border-b border-slate-800 p-5 md:flex-row md:items-end md:justify-between">
        <div>
          <h2 className="font-semibold text-white">계정 관리</h2>
          <p className="mt-1 text-xs text-slate-500">계정 정지·복구와 영구 삭제를 관리합니다.</p>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="회사, 이메일, 담당자 검색" className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-violet-500 sm:w-64" />
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-violet-500">
            <option value="all">전체 상태</option>
            <option value="active">정상</option>
            <option value="suspended">정지</option>
          </select>
        </div>
      </div>
      <AccountTable users={users} onOpenUser={onOpenUser} />
    </section>
  )
}

function AccountTable({ users, onOpenUser }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[900px] text-left text-sm">
        <thead className="bg-slate-950/50 text-xs text-slate-500">
          <tr><th className="px-5 py-3">회사 / 계정</th><th className="px-5 py-3">담당자</th><th className="px-5 py-3">현장</th><th className="px-5 py-3">위험 기록</th><th className="px-5 py-3">마지막 로그인</th><th className="px-5 py-3">상태</th><th className="px-5 py-3">관리</th></tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {users.length ? users.map((user) => (
            <tr key={user.id} className="text-slate-300 hover:bg-slate-800/40">
              <td className="px-5 py-3"><p className="font-medium text-white">{user.company_name}</p><p className="mt-0.5 text-xs text-slate-500">{user.email}{user.role === 'platform_admin' ? ' · 관리자' : ''}</p></td>
              <td className="px-5 py-3">{user.manager_name}</td>
              <td className="px-5 py-3 tabular-nums">{user.site_count}곳</td>
              <td className="px-5 py-3 tabular-nums">{user.event_count}건 <span className="text-red-300">({user.unresolved_count} 미조치)</span></td>
              <td className="px-5 py-3 text-xs text-slate-500">{formatDate(user.last_login_at)}</td>
              <td className="px-5 py-3"><StatusBadge status={user.status} /></td>
              <td className="px-5 py-3"><button onClick={() => onOpenUser(user.id)} className="rounded-md border border-slate-700 px-2.5 py-1.5 text-xs hover:border-violet-500 hover:text-violet-300">상세</button></td>
            </tr>
          )) : <tr><td colSpan="7" className="px-5 py-10 text-center text-slate-500">조건에 맞는 계정이 없습니다.</td></tr>}
        </tbody>
      </table>
    </div>
  )
}

function UserDetail({ user, currentAdminId, onClose, onChangeStatus, onDelete }) {
  const [showDelete, setShowDelete] = useState(false)
  const [confirmation, setConfirmation] = useState('')
  const [deleting, setDeleting] = useState(false)
  const isSelf = user.id === currentAdminId

  const submitDelete = async (event) => {
    event.preventDefault()
    setDeleting(true)
    try {
      await onDelete(user, confirmation)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/65" role="dialog" aria-modal="true" aria-label="계정 상세">
      <div className="h-full w-full max-w-2xl overflow-y-auto border-l border-slate-800 bg-[#0b1524] p-5 shadow-2xl sm:p-7">
        <div className="flex items-start justify-between gap-4">
          <div><p className="text-xs font-semibold tracking-wider text-violet-400">ACCOUNT DETAIL</p><h2 className="mt-1 text-2xl font-bold text-white">{user.company_name}</h2><p className="mt-1 text-sm text-slate-400">{user.email}</p></div>
          <button onClick={onClose} className="rounded-lg border border-slate-700 px-3 py-2 text-sm text-slate-400 hover:text-white">닫기</button>
        </div>

        <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <DetailMetric label="현장" value={`${user.site_count}곳`} />
          <DetailMetric label="작업자" value={`${user.worker_count}명`} />
          <DetailMetric label="카메라" value={`${user.camera_count}대`} />
          <DetailMetric label="위험 기록" value={`${user.event_count}건`} />
        </div>

        <section className="mt-6 rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h3 className="font-semibold text-white">계정 정보</h3>
          <dl className="mt-4 grid gap-4 text-sm sm:grid-cols-2">
            <Info label="담당자" value={user.manager_name} />
            <Info label="역할" value={user.role === 'platform_admin' ? '서버 관리자' : '일반 사용자'} />
            <Info label="가입일" value={formatDate(user.created_at)} />
            <Info label="마지막 로그인" value={formatDate(user.last_login_at)} />
          </dl>
        </section>

        <section className="mt-5 overflow-hidden rounded-xl border border-slate-800 bg-slate-900">
          <div className="border-b border-slate-800 px-5 py-4"><h3 className="font-semibold text-white">소속 현장</h3></div>
          {user.sites.length ? user.sites.map((site) => (
            <div key={site.id} className="flex flex-col gap-2 border-b border-slate-800 px-5 py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
              <div><p className="font-medium text-white">{site.name}</p><p className="mt-1 text-xs text-slate-500">현장 ID {site.id}</p></div>
              <p className="text-xs text-slate-400">작업자 {site.worker_count} · 카메라 {site.camera_count} · 구역 {site.zone_count} · 기록 {site.event_count}</p>
            </div>
          )) : <p className="px-5 py-8 text-center text-sm text-slate-500">등록된 현장이 없습니다.</p>}
        </section>

        <section className="mt-5 rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h3 className="font-semibold text-white">계정 제어</h3>
          {isSelf ? <p className="mt-3 text-sm text-amber-300">현재 로그인한 관리자 계정은 정지하거나 삭제할 수 없습니다.</p> : (
            <div className="mt-4 flex flex-wrap gap-2">
              {user.status === 'active' ? (
                <button onClick={() => onChangeStatus(user, 'suspend')} className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm font-medium text-amber-300 hover:bg-amber-500/20">계정 정지</button>
              ) : (
                <button onClick={() => onChangeStatus(user, 'activate')} className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-2 text-sm font-medium text-emerald-300 hover:bg-emerald-500/20">계정 복구</button>
              )}
              <button onClick={() => setShowDelete(true)} className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-medium text-red-300 hover:bg-red-500/20">영구 삭제</button>
            </div>
          )}
        </section>

        {showDelete && (
          <form onSubmit={submitDelete} className="mt-5 rounded-xl border border-red-500/40 bg-red-500/10 p-5">
            <h3 className="font-semibold text-red-200">계정을 영구 삭제하시겠습니까?</h3>
            <p className="mt-2 text-sm leading-6 text-red-200/80">현장 {user.site_count}곳, 작업자 {user.worker_count}명, 카메라 {user.camera_count}대, 위험 기록 {user.event_count}건이 함께 삭제되며 복구할 수 없습니다.</p>
            <label className="mt-4 block text-sm text-red-100">확인을 위해 <strong>{user.email}</strong>을 입력하세요.
              <input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} className="mt-2 w-full rounded-lg border border-red-500/40 bg-slate-950 px-3 py-2 text-white outline-none focus:border-red-400" />
            </label>
            <div className="mt-4 flex gap-2">
              <button type="button" onClick={() => { setShowDelete(false); setConfirmation('') }} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-300">취소</button>
              <button disabled={confirmation !== user.email || deleting} className="rounded-lg bg-red-500 px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40">{deleting ? '삭제 중...' : '영구 삭제'}</button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

function AllEvents({ events, deletingScope, onDeleteToday, onDeleteAll }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
      <div className="flex flex-col gap-4 border-b border-slate-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="font-semibold text-white">전체 위험 기록</h2>
          <p className="mt-1 text-xs text-slate-500">모든 계정과 현장에서 발생한 최근 위험 기록입니다.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={onDeleteToday} disabled={Boolean(deletingScope)} className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs font-semibold text-amber-300 hover:bg-amber-500/20 disabled:cursor-wait disabled:opacity-50">
            {deletingScope === 'today' ? '삭제 중...' : '오늘 이벤트 삭제'}
          </button>
          <button onClick={onDeleteAll} disabled={Boolean(deletingScope)} className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-300 hover:bg-red-500/20 disabled:cursor-wait disabled:opacity-50">
            {deletingScope === 'all' ? '삭제 중...' : '전체 이벤트 삭제'}
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[850px] text-left text-sm">
          <thead className="bg-slate-950/50 text-xs text-slate-500"><tr><th className="px-5 py-3">발생 시각</th><th className="px-5 py-3">회사</th><th className="px-5 py-3">현장</th><th className="px-5 py-3">유형</th><th className="px-5 py-3">신뢰도</th><th className="px-5 py-3">상태</th></tr></thead>
          <tbody className="divide-y divide-slate-800">
            {events.length ? events.map((event) => <tr key={event.id} className="text-slate-300"><td className="px-5 py-3 text-xs text-slate-500">{formatDate(event.timestamp)}</td><td className="px-5 py-3">{event.company_name}</td><td className="px-5 py-3">{event.site_name}</td><td className="px-5 py-3 text-red-300">{event.event_type}</td><td className="px-5 py-3">{Math.round(event.confidence * 100)}%</td><td className="px-5 py-3">{event.resolved ? <span className="text-emerald-400">조치 완료</span> : <span className="text-red-300">미조치</span>}</td></tr>) : <tr><td colSpan="6" className="px-5 py-10 text-center text-slate-500">위험 기록이 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function AuditLogs({ logs }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900">
      <SectionHeader title="관리자 감사 로그" description="계정 정지, 복구, 삭제 등 중요한 관리자 작업을 보존합니다." />
      <div className="divide-y divide-slate-800">
        {logs.length ? logs.map((log) => (
          <article key={log.id} className="flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div><p className="text-sm text-slate-200"><span className="font-medium text-violet-300">{log.admin_email}</span> · {actionLabels[log.action] ?? log.action}</p><p className="mt-1 text-xs text-slate-500">대상: {log.target_email ?? '시스템'}</p></div>
            <time className="text-xs text-slate-500">{formatDate(log.created_at)}</time>
          </article>
        )) : <p className="px-5 py-10 text-center text-sm text-slate-500">관리자 작업 기록이 없습니다.</p>}
      </div>
    </section>
  )
}

function AdminMetric({ label, value, unit, tone }) {
  const colors = { cyan: 'text-cyan-300', violet: 'text-violet-300', emerald: 'text-emerald-300', blue: 'text-blue-300', amber: 'text-amber-300', red: 'text-red-300' }
  return <div className="rounded-xl border border-slate-800 bg-slate-900 p-4"><p className="text-xs text-slate-500">{label}</p><p className={`mt-2 text-2xl font-bold text-white ${colors[tone]}`}><span className="text-white">{value}</span> <span className="text-xs font-medium text-slate-500">{unit}</span></p></div>
}

function SectionHeader({ title, description }) {
  return <div className="border-b border-slate-800 px-5 py-4"><h2 className="font-semibold text-white">{title}</h2><p className="mt-1 text-xs text-slate-500">{description}</p></div>
}

function StatusBadge({ status }) {
  return status === 'active' ? <span className="rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-300">정상</span> : <span className="rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300">정지</span>
}

function SystemStatus({ label, value, ok }) {
  return <div className="flex items-center justify-between text-sm"><span className="text-slate-400">{label}</span><span className={ok ? 'text-emerald-300' : 'text-amber-300'}>{value}</span></div>
}

function DetailMetric({ label, value }) {
  return <div className="rounded-lg border border-slate-800 bg-slate-950/50 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 font-semibold text-white">{value}</p></div>
}

function Info({ label, value }) {
  return <div><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 text-slate-200">{value || '-'}</dd></div>
}

function AdminLoading() {
  return <div className="grid min-h-[300px] place-items-center"><div className="text-center"><div className="mx-auto mb-3 h-9 w-9 animate-spin rounded-full border-2 border-slate-700 border-t-violet-400" /><p className="text-sm text-slate-500">관리 정보를 불러오는 중...</p></div></div>
}

function formatDate(value) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('ko-KR', {
    year: '2-digit', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}
