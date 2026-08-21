import { useCallback, useEffect, useRef, useState } from 'react'
import { bindBleTag, mergePersonIds, submitBleObservation, unbindBleTag } from './api'

const IBEACON_PATTERN = /OK\+DISC:?([0-9a-f]{8}):([0-9a-f]{32}):([0-9a-f]{10}):([0-9a-f]{12}):([0-9a-f]{4}|-\d{2,3})/gi

function signedHex(raw, bits) {
  const value = Number.parseInt(raw, 16)
  if (!Number.isFinite(value)) return null
  const limit = 2 ** bits
  return value >= limit / 2 ? value - limit : value
}

function parseRssi(raw) {
  if (raw.startsWith('-')) return Number.parseInt(raw, 10)
  return signedHex(raw, raw.length <= 2 ? 8 : 16)
}

function parseBeacon(match) {
  const payload = match[3]
  const measuredPower = signedHex(payload.slice(8, 10), 8)
  return {
    uuid: match[2].toUpperCase(),
    major: Number.parseInt(payload.slice(0, 4), 16),
    minor: Number.parseInt(payload.slice(4, 8), 16),
    measured_power: Number.isFinite(measuredPower) ? measuredPower : null,
    rssi: parseRssi(match[5]),
  }
}

function BleReceiver({ receiverId, label, onRequestError }) {
  const portRef = useRef(null)
  const readerRef = useRef(null)
  const stoppedRef = useRef(false)
  const bufferRef = useRef('')
  const lastSentRef = useRef(new Map())
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const supported = typeof navigator !== 'undefined' && 'serial' in navigator

  const handleError = useCallback((requestError) => {
    setError(requestError.message || 'BLE 정보를 서버로 보내지 못했습니다.')
    onRequestError?.(requestError)
  }, [onRequestError])

  const submitChunk = useCallback((chunk) => {
    bufferRef.current += chunk
    IBEACON_PATTERN.lastIndex = 0
    let match
    let consumed = 0
    while ((match = IBEACON_PATTERN.exec(bufferRef.current)) !== null) {
      consumed = IBEACON_PATTERN.lastIndex
      const observation = parseBeacon(match)
      if (!Number.isFinite(observation.rssi) || observation.rssi < -127 || observation.rssi > 20) continue
      const key = [observation.uuid, observation.major, observation.minor].join(':')
      const now = Date.now()
      if (now - (lastSentRef.current.get(key) || 0) < 500) continue
      lastSentRef.current.set(key, now)
      submitBleObservation({ ...observation, receiver_id: receiverId }).catch(handleError)
    }
    if (consumed) bufferRef.current = bufferRef.current.slice(consumed)
    if (bufferRef.current.length > 2048) bufferRef.current = bufferRef.current.slice(-256)
  }, [handleError, receiverId])

  const disconnect = useCallback(async () => {
    stoppedRef.current = true
    const reader = readerRef.current
    readerRef.current = null
    if (reader) {
      try { await reader.cancel() } catch { /* already disconnected */ }
      try { reader.releaseLock() } catch { /* already released */ }
    }
    const port = portRef.current
    portRef.current = null
    if (port) {
      try { await port.close() } catch { /* physical disconnect */ }
    }
    bufferRef.current = ''
    setStatus('idle')
  }, [])

  useEffect(() => () => { disconnect() }, [disconnect])

  const readLoop = useCallback(async (port) => {
    const decoder = new TextDecoder()
    const reader = port.readable.getReader()
    readerRef.current = reader
    try {
      while (!stoppedRef.current) {
        const { value, done } = await reader.read()
        if (done) break
        if (value) submitChunk(decoder.decode(value, { stream: true }))
      }
    } catch (readError) {
      if (!stoppedRef.current) setError(readError.message || 'Arduino USB 연결이 끊어졌습니다.')
    } finally {
      try { reader.releaseLock() } catch { /* already released */ }
      if (readerRef.current === reader) readerRef.current = null
      if (!stoppedRef.current) setStatus('idle')
    }
  }, [submitChunk])

  const connect = async () => {
    if (!supported) {
      setError('데스크톱 Chrome 또는 Edge의 HTTPS 페이지에서 연결하세요.')
      return
    }
    setError('')
    setStatus('requesting')
    try {
      const port = await navigator.serial.requestPort()
      await port.open({ baudRate: 9600, bufferSize: 1024 })
      portRef.current = port
      stoppedRef.current = false
      setStatus('connected')
      readLoop(port)
    } catch (connectError) {
      setStatus('idle')
      if (connectError?.name !== 'NotFoundError') {
        setError(connectError.message || 'Arduino USB 포트를 열지 못했습니다.')
      }
    }
  }

  return (
    <article className="rounded-xl border border-violet-500/20 bg-slate-950/40 p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-white">{label}</p>
          <p className="text-[11px] text-slate-500">{receiverId} · 같은 번호의 카메라 옆에 고정</p>
        </div>
        {status === 'connected' ? (
          <button onClick={disconnect} className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-300">
            연결 해제
          </button>
        ) : (
          <button
            onClick={connect}
            disabled={status === 'requesting'}
            className="rounded-lg bg-violet-500 px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
          >
            {status === 'requesting' ? '포트 선택 중...' : 'USB 연결'}
          </button>
        )}
      </div>
      <p className={'mt-2 text-xs ' + (status === 'connected' ? 'text-emerald-300' : 'text-slate-600')}>
        {status === 'connected' ? 'iBeacon 신호 수신 중' : 'Arduino 포트를 선택하세요.'}
      </p>
      {error && <p role="alert" className="mt-2 text-xs text-red-300">{error}</p>}
    </article>
  )
}

export default function BleSerialController({ tags = [], workers = [], onRequestError }) {
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [selectedTracks, setSelectedTracks] = useState({})
  const [mergePrimary, setMergePrimary] = useState('')
  const [mergeDuplicate, setMergeDuplicate] = useState('')

  const bind = async (tag) => {
    const trackId = selectedTracks[tag.tag_key]
    if (!trackId) {
      setError('태그와 연결할 현재 작업자 ID를 선택하세요.')
      return
    }
    try {
      setError('')
      const result = await bindBleTag(tag.tag_key, trackId)
      const merged = result.merged_track_ids ?? []
      setNotice(
        'BLE ' + tag.major + '/' + tag.minor + '를 ' + trackId + '에 등록했습니다.'
        + (merged.length ? ' 같은 사람으로 감지된 ' + merged.join(', ') + '도 자동 병합했습니다.' : ''),
      )
    } catch (requestError) {
      setError(requestError.message)
      onRequestError?.(requestError)
    }
  }

  const mergeIdentities = async () => {
    if (!mergePrimary || !mergeDuplicate) {
      setError('유지할 ID와 합칠 ID를 모두 선택하세요.')
      return
    }
    if (mergePrimary === mergeDuplicate) {
      setError('서로 다른 두 ID를 선택하세요.')
      return
    }
    try {
      setError('')
      const result = await mergePersonIds(mergePrimary, mergeDuplicate)
      setNotice(
        result.merged_track_ids.join(', ')
        + '을(를) ' + result.primary_track_id + '으로 병합했습니다.',
      )
      setMergeDuplicate('')
    } catch (requestError) {
      setError(requestError.message)
      onRequestError?.(requestError)
    }
  }

  const unbind = async (tag) => {
    try {
      setError('')
      await unbindBleTag(tag.tag_key)
      setNotice('BLE ' + tag.major + '/' + tag.minor + ' 등록을 해제했습니다.')
    } catch (requestError) {
      setError(requestError.message)
      onRequestError?.(requestError)
    }
  }

  return (
    <section className="mb-5 rounded-2xl border border-violet-500/20 bg-violet-500/5 p-4">
      <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
        <div>
          <h2 className="font-semibold text-white">BLE 작업자 태그 · 수신기 2대</h2>
          <p className="mt-1 text-xs text-slate-500">수신기 A는 카메라 A 옆, 수신기 B는 카메라 B 옆에 두면 상대 RSSI가 카메라 간 ID 유지에 사용됩니다.</p>
        </div>
        <a href="./hm10_ibeacon_scanner.ino" download className="rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-300 hover:border-violet-500 hover:text-violet-300">
          Arduino 코드 받기
        </a>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <BleReceiver receiverId="receiver-1" label="BLE 수신기 A" onRequestError={onRequestError} />
        <BleReceiver receiverId="receiver-2" label="BLE 수신기 B" onRequestError={onRequestError} />
      </div>

      <p className="mt-3 text-xs text-slate-400">
        최초 등록은 한 사람씩 카메라 앞에 세워 태그와 ID를 연결하세요. 두 태그는 UUID가 같아도 Major/Minor 조합은 서로 달라야 합니다.
      </p>

      <article className="mt-3 rounded-xl border border-cyan-500/20 bg-cyan-500/5 p-3">
        <div>
          <p className="text-sm font-semibold text-white">같은 사람 ID 수동 병합</p>
          <p className="mt-1 text-xs text-slate-500">
            같은 사람이 카메라별로 다른 ID로 보일 때, 비콘이 표시되는 ID를 유지 ID로 선택하세요.
          </p>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto_1fr_auto] sm:items-center">
          <select
            value={mergePrimary}
            onChange={(event) => setMergePrimary(event.target.value)}
            className="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-xs text-white"
          >
            <option value="">유지할 ID</option>
            {workers.map((worker) => (
              <option key={'primary-' + worker.track_id} value={worker.track_id}>
                {worker.track_id} · {(worker.camera_ids ?? [worker.camera_id]).filter(Boolean).join(', ')}
              </option>
            ))}
          </select>
          <span className="hidden text-xs text-slate-500 sm:inline">←</span>
          <select
            value={mergeDuplicate}
            onChange={(event) => setMergeDuplicate(event.target.value)}
            className="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-xs text-white"
          >
            <option value="">합칠 ID</option>
            {workers.map((worker) => (
              <option key={'duplicate-' + worker.track_id} value={worker.track_id}>
                {worker.track_id} · {(worker.camera_ids ?? [worker.camera_id]).filter(Boolean).join(', ')}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={mergeIdentities}
            disabled={workers.length < 2 || !mergePrimary || !mergeDuplicate}
            className="rounded-lg bg-cyan-500/20 px-3 py-2 text-xs font-semibold text-cyan-200 disabled:opacity-40"
          >
            같은 사람으로 병합
          </button>
        </div>
      </article>

      <div className="mt-3 grid gap-3 lg:grid-cols-2">
        {tags.length ? tags.map((tag) => (
          <article key={tag.tag_key} className="rounded-xl border border-slate-800 bg-slate-950/50 p-3">
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="text-sm font-semibold text-white">BLE {tag.major}/{tag.minor}</p>
                <p className="mt-0.5 font-mono text-[10px] text-slate-600">{tag.uuid}</p>
              </div>
              <span className={'rounded-full px-2 py-1 text-[10px] font-bold ' + (tag.active ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-500')}>
                {tag.active ? '수신 중' : '신호 만료'}
              </span>
            </div>

            <div className="mt-2 flex flex-wrap gap-2">
              {(tag.receivers ?? []).map((receiver) => (
                <span key={receiver.receiver_id} className={'rounded-md px-2 py-1 text-[10px] ' + (receiver.active ? 'bg-violet-500/15 text-violet-200' : 'bg-slate-800 text-slate-500')}>
                  {receiver.receiver_id === 'receiver-1' ? 'A' : 'B'} · {receiver.rssi} dBm
                </span>
              ))}
            </div>

            {tag.bound_track_id ? (
              <div className="mt-3 flex items-center justify-between gap-2 rounded-lg bg-violet-500/10 px-3 py-2">
                <span className="text-xs text-violet-200">연결됨 · {tag.bound_track_id}</span>
                <button onClick={() => unbind(tag)} className="text-xs text-slate-400 hover:text-red-300">해제</button>
              </div>
            ) : (
              <div className="mt-3 flex gap-2">
                <select
                  value={selectedTracks[tag.tag_key] || ''}
                  onChange={(event) => setSelectedTracks((current) => ({ ...current, [tag.tag_key]: event.target.value }))}
                  className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-2 py-2 text-xs text-white"
                >
                  <option value="">현재 작업자 선택</option>
                  {workers.map((worker) => (
                    <option key={worker.track_id} value={worker.track_id}>{worker.track_id}</option>
                  ))}
                </select>
                <button onClick={() => bind(tag)} disabled={!tag.active || !workers.length} className="rounded-lg bg-violet-500/20 px-3 py-2 text-xs font-semibold text-violet-200 disabled:opacity-40">
                  등록
                </button>
              </div>
            )}
          </article>
        )) : (
          <p className="rounded-xl border border-dashed border-slate-800 px-4 py-6 text-center text-xs text-slate-600 lg:col-span-2">
            두 Arduino를 연결하면 감지된 iBeacon 태그가 여기에 표시됩니다.
          </p>
        )}
      </div>

      {notice && <p className="mt-3 text-xs text-emerald-300">{notice}</p>}
      {error && <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
    </section>
  )
}
