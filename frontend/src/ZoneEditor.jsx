import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'

const EMPTY_FORM = {
  name: '',
  zone_type: 'no_entry',
  risk_level: 'high',
  description: '',
  precautions: '',
  visible: true,
}

const RISK_LEVELS = {
  low: { label: '낮음', color: '#22c55e', badge: 'bg-emerald-500/15 text-emerald-300' },
  medium: { label: '보통', color: '#eab308', badge: 'bg-yellow-500/15 text-yellow-300' },
  high: { label: '높음', color: '#f97316', badge: 'bg-orange-500/15 text-orange-300' },
  critical: { label: '매우 높음', color: '#ef4444', badge: 'bg-red-500/15 text-red-300' },
}

const ZONE_TYPES = {
  no_entry: '출입금지',
  fall_risk: '추락위험',
  heavy_equip: '중장비 작업반경',
  camera_entry: '카메라 입구 ROI',
  camera_exit: '카메라 출구 ROI',
}

const clamp = (value) => Math.min(1, Math.max(0, value))
const svgPoints = (points) => points.map(([x, y]) => `${x * 1000},${y * 1000}`).join(' ')

function zoneCenter(points) {
  const total = points.reduce((result, [x, y]) => [result[0] + x, result[1] + y], [0, 0])
  return [total[0] / points.length, total[1] / points.length]
}

export default function ZoneEditor({
  siteId,
  cameraId,
  streamKey,
  streamSrc,
  streamAlt,
  streamReady,
  waitingMessage,
  streamError,
  onStreamLoad,
  onStreamError,
  onRequestError,
  onRegisterFrameCallback,
}) {
  const containerRef = useRef(null)
  const imageRef = useRef(null)
  const [imgSrc, setImgSrc] = useState(onRegisterFrameCallback ? null : streamSrc)

  useEffect(() => {
    if (!onRegisterFrameCallback) return undefined
    onRegisterFrameCallback((base64data) => {
      setImgSrc(`data:image/jpeg;base64,${base64data}`)
    })
    return () => onRegisterFrameCallback(null)
  }, [onRegisterFrameCallback])
  const svgRef = useRef(null)
  const dragRef = useRef(null)
  const visibilityKey = `safety_zone_overlay_${siteId}_${cameraId ?? 'default'}`
  const [zones, setZones] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [overlayVisible, setOverlayVisible] = useState(() => {
    try {
      return window.localStorage.getItem(visibilityKey) !== '0'
    } catch {
      return true
    }
  })
  const [displayRect, setDisplayRect] = useState(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [phase, setPhase] = useState('idle')
  const [tool, setTool] = useState('vertices')
  const [editingZoneId, setEditingZoneId] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [points, setPoints] = useState([])
  const [history, setHistory] = useState([])
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')

  const zonesUrl = cameraId ? `/api/zones?camera_id=${cameraId}` : '/api/zones'

  const loadZones = useCallback(async () => {
    try {
      const nextZones = await api(zonesUrl)
      setZones(nextZones)
      setLoadError('')
    } catch (error) {
      setLoadError(error.message)
      onRequestError(error)
    } finally {
      setLoading(false)
    }
  }, [onRequestError, zonesUrl])

  useEffect(() => {
    const timer = window.setTimeout(loadZones, 0)
    return () => window.clearTimeout(timer)
  }, [loadZones])

  const updateDisplayRect = useCallback(() => {
    const container = containerRef.current
    const image = imageRef.current
    if (!container || !image?.naturalWidth || !image?.naturalHeight) return
    const containerWidth = container.clientWidth
    const containerHeight = container.clientHeight
    const scale = Math.min(containerWidth / image.naturalWidth, containerHeight / image.naturalHeight)
    const width = image.naturalWidth * scale
    const height = image.naturalHeight * scale
    setDisplayRect({
      left: (containerWidth - width) / 2,
      top: (containerHeight - height) / 2,
      width,
      height,
    })
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined
    const observer = new ResizeObserver(updateDisplayRect)
    observer.observe(container)
    const frame = window.requestAnimationFrame(updateDisplayRect)
    return () => {
      observer.disconnect()
      window.cancelAnimationFrame(frame)
    }
  }, [updateDisplayRect])

  const handleImageLoad = () => {
    updateDisplayRect()
    onStreamLoad()
  }

  const setGlobalVisibility = () => {
    const next = !overlayVisible
    setOverlayVisible(next)
    try {
      window.localStorage.setItem(visibilityKey, next ? '1' : '0')
    } catch {
      // 표시 여부는 장치 편의 설정이므로 저장 실패 시 현재 세션에서만 유지한다.
    }
  }

  const resetEditor = () => {
    setEditorOpen(false)
    setPhase('idle')
    setEditingZoneId(null)
    setForm(EMPTY_FORM)
    setPoints([])
    setHistory([])
    setTool('vertices')
    dragRef.current = null
  }

  const beginCreate = () => {
    setEditorOpen(true)
    setPhase('details')
    setEditingZoneId(null)
    setForm(EMPTY_FORM)
    setPoints([])
    setHistory([])
    setTool('vertices')
    setNotice('먼저 구역 정보를 입력한 뒤 그리기를 시작하세요.')
  }

  const beginDrawing = () => {
    if (!form.name.trim()) {
      setNotice('구역 이름을 먼저 입력하세요.')
      return
    }
    setPhase('drawing')
    setNotice('영상 위를 차례로 클릭하세요. 첫 점을 다시 누르거나 미리보기를 누르면 영역이 닫힙니다.')
  }

  const beginEdit = (zone) => {
    setEditorOpen(true)
    setPhase('editing')
    setEditingZoneId(zone.id)
    setForm({
      name: zone.name,
      zone_type: zone.zone_type,
      risk_level: zone.risk_level,
      description: zone.description,
      precautions: zone.precautions,
      visible: zone.visible,
    })
    setPoints(zone.polygon)
    setHistory([])
    setTool('vertices')
    setNotice('점을 드래그해 모양을 바꾸거나 위치 이동 도구로 영역 전체를 옮기세요.')
  }

  const normalizedPointer = (event) => {
    const rect = svgRef.current.getBoundingClientRect()
    return [clamp((event.clientX - rect.left) / rect.width), clamp((event.clientY - rect.top) / rect.height)]
  }

  const addPoint = (event) => {
    if (phase !== 'drawing' || dragRef.current) return
    const nextPoint = normalizedPointer(event)
    if (points.length >= 3) {
      const [firstX, firstY] = points[0]
      if (Math.hypot(nextPoint[0] - firstX, nextPoint[1] - firstY) < 0.035) {
        setPhase('preview')
        setNotice('미리보기입니다. 모양과 정보를 확인한 뒤 저장하세요.')
        return
      }
    }
    if (points.length >= 50) {
      setNotice('점은 최대 50개까지 선택할 수 있습니다.')
      return
    }
    setHistory((current) => [...current, points])
    setPoints((current) => [...current, nextPoint])
  }

  const beginVertexDrag = (event, index) => {
    if (phase !== 'drawing' && phase !== 'editing') return
    event.preventDefault()
    event.stopPropagation()
    setHistory((current) => [...current, points])
    dragRef.current = { type: 'vertex', index, original: points, pointerId: event.pointerId }
    svgRef.current.setPointerCapture(event.pointerId)
  }

  const handleVertexClick = (event, index) => {
    event.stopPropagation()
    if (phase === 'drawing' && index === 0 && points.length >= 3) {
      setPhase('preview')
      setNotice('미리보기입니다. 모양과 정보를 확인한 뒤 저장하세요.')
    }
  }

  const beginMove = (event) => {
    if (phase !== 'editing' || tool !== 'move') return
    event.preventDefault()
    event.stopPropagation()
    const start = normalizedPointer(event)
    setHistory((current) => [...current, points])
    dragRef.current = { type: 'move', start, original: points, pointerId: event.pointerId }
    svgRef.current.setPointerCapture(event.pointerId)
  }

  const handlePointerMove = (event) => {
    const drag = dragRef.current
    if (!drag || drag.pointerId !== event.pointerId) return
    const pointer = normalizedPointer(event)
    if (drag.type === 'vertex') {
      setPoints(drag.original.map((point, index) => index === drag.index ? pointer : point))
      return
    }
    const dx = pointer[0] - drag.start[0]
    const dy = pointer[1] - drag.start[1]
    const minX = Math.min(...drag.original.map(([x]) => x))
    const maxX = Math.max(...drag.original.map(([x]) => x))
    const minY = Math.min(...drag.original.map(([, y]) => y))
    const maxY = Math.max(...drag.original.map(([, y]) => y))
    const safeDx = Math.min(1 - maxX, Math.max(-minX, dx))
    const safeDy = Math.min(1 - maxY, Math.max(-minY, dy))
    setPoints(drag.original.map(([x, y]) => [x + safeDx, y + safeDy]))
  }

  const finishPointerDrag = (event) => {
    if (dragRef.current?.pointerId !== event.pointerId) return
    if (svgRef.current.hasPointerCapture(event.pointerId)) svgRef.current.releasePointerCapture(event.pointerId)
    dragRef.current = null
  }

  const undoPointChange = () => {
    if (!history.length) return
    setPoints(history[history.length - 1])
    setHistory((current) => current.slice(0, -1))
    setNotice('마지막 변경을 되돌렸습니다.')
  }

  const showPreview = () => {
    if (points.length < 3) {
      setNotice('위험구역을 만들려면 점을 3개 이상 선택하세요.')
      return
    }
    setPhase('preview')
    setNotice('미리보기입니다. 모양과 정보를 확인한 뒤 저장하세요.')
  }

  const continueEditing = () => {
    setPhase(editingZoneId ? 'editing' : 'drawing')
    setNotice('구역 모양을 계속 수정할 수 있습니다.')
  }

  const saveZone = async () => {
    if (phase !== 'preview' || saving) return
    setSaving(true)
    setNotice('저장 중...')
    const payload = {
      ...form,
      name: form.name.trim(),
      description: form.description.trim(),
      precautions: form.precautions.trim(),
      camera_id: cameraId,
      polygon: points.map(([x, y]) => [Number(x.toFixed(6)), Number(y.toFixed(6))]),
    }
    try {
      const saved = await api(editingZoneId ? `/api/zones/${editingZoneId}` : '/api/zones', {
        method: editingZoneId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      })
      setZones((current) => editingZoneId
        ? current.map((zone) => zone.id === saved.id ? saved : zone)
        : [...current, saved])
      resetEditor()
      setNotice(`‘${saved.name}’ 위험구역 저장 완료`)
    } catch (error) {
      setNotice(error.message)
      onRequestError(error)
    } finally {
      setSaving(false)
    }
  }

  const toggleZoneVisibility = async (zone) => {
    try {
      const saved = await api(`/api/zones/${zone.id}/visibility`, {
        method: 'PATCH',
        body: JSON.stringify({ visible: !zone.visible }),
      })
      setZones((current) => current.map((item) => item.id === saved.id ? saved : item))
      setNotice(`‘${saved.name}’ 표시 설정이 저장되었습니다.`)
    } catch (error) {
      setNotice(error.message)
      onRequestError(error)
    }
  }

  const deleteZone = async (zone) => {
    if (!window.confirm(`‘${zone.name}’ 위험구역을 삭제할까요?`)) return
    try {
      await api(`/api/zones/${zone.id}`, { method: 'DELETE' })
      setZones((current) => current.filter((item) => item.id !== zone.id))
      if (editingZoneId === zone.id) resetEditor()
      setNotice(`‘${zone.name}’ 위험구역을 삭제했습니다.`)
    } catch (error) {
      setNotice(error.message)
      onRequestError(error)
    }
  }

  const draftColor = RISK_LEVELS[form.risk_level].color
  const interactive = editorOpen && (phase === 'drawing' || phase === 'editing')

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-950/40 px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <button type="button" onClick={setGlobalVisibility} className={`rounded-md border px-2.5 py-1.5 ${overlayVisible ? 'border-cyan-500/40 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400'}`}>
            구역 표시 {overlayVisible ? '켜짐' : '꺼짐'}
          </button>
          <span className="text-slate-500">저장 구역 {zones.length}개 · {cameraId ? `카메라 #${cameraId}` : '기본 영상'}</span>
          {loading && <span className="text-slate-500">불러오는 중...</span>}
        </div>
        <button type="button" onClick={beginCreate} disabled={!streamReady || editorOpen} className="rounded-md bg-cyan-500 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40">
          새 위험구역 설정
        </button>
      </div>

      <div ref={containerRef} className="relative aspect-video overflow-hidden bg-black">
        <img
          ref={imageRef}
          key={streamKey}
          className="h-full w-full object-contain"
          src={imgSrc ?? undefined}
          alt={streamAlt}
          onLoad={imgSrc ? handleImageLoad : undefined}
          onError={imgSrc ? onStreamError : undefined}
        />

        {displayRect && (
          <svg
            ref={svgRef}
            viewBox="0 0 1000 1000"
            preserveAspectRatio="none"
            aria-label="위험구역 편집 화면"
            onClick={addPoint}
            onPointerMove={handlePointerMove}
            onPointerUp={finishPointerDrag}
            onPointerCancel={finishPointerDrag}
            className={`absolute z-20 select-none ${interactive ? tool === 'move' ? 'cursor-move' : 'cursor-crosshair' : 'pointer-events-none'}`}
            style={{ left: displayRect.left, top: displayRect.top, width: displayRect.width, height: displayRect.height, touchAction: 'none' }}
          >
            {overlayVisible && zones.filter((zone) => zone.visible && zone.id !== editingZoneId).map((zone) => {
              const color = RISK_LEVELS[zone.risk_level]?.color || RISK_LEVELS.high.color
              const [labelX, labelY] = zoneCenter(zone.polygon)
              return (
                <g key={zone.id}>
                  <polygon points={svgPoints(zone.polygon)} fill={color} fillOpacity="0.2" stroke={color} strokeWidth="4" vectorEffect="non-scaling-stroke" />
                  <text x={labelX * 1000} y={labelY * 1000} textAnchor="middle" dominantBaseline="middle" fill="white" stroke="#020617" strokeWidth="5" paintOrder="stroke" fontSize="30" fontWeight="700">{zone.name}</text>
                </g>
              )
            })}

            {editorOpen && points.length > 0 && (
              <g>
                {points.length >= 3 && (
                  <polygon
                    points={svgPoints(points)}
                    fill={draftColor}
                    fillOpacity={phase === 'preview' ? '0.36' : '0.2'}
                    stroke={draftColor}
                    strokeWidth="5"
                    strokeDasharray={phase === 'preview' ? '0' : '12 8'}
                    vectorEffect="non-scaling-stroke"
                    onPointerDown={beginMove}
                    className={phase === 'editing' && tool === 'move' ? 'pointer-events-auto cursor-move' : ''}
                  />
                )}
                {points.length < 3 && <polyline points={svgPoints(points)} fill="none" stroke={draftColor} strokeWidth="5" strokeDasharray="12 8" vectorEffect="non-scaling-stroke" />}
                {phase !== 'preview' && points.map(([x, y], index) => (
                  <circle
                    key={`${index}-${x}-${y}`}
                    cx={x * 1000}
                    cy={y * 1000}
                    r={index === 0 && phase === 'drawing' ? '18' : '13'}
                    fill={index === 0 ? '#ffffff' : draftColor}
                    stroke="#020617"
                    strokeWidth="5"
                    vectorEffect="non-scaling-stroke"
                    onPointerDown={(event) => beginVertexDrag(event, index)}
                    onClick={(event) => handleVertexClick(event, index)}
                    className="pointer-events-auto cursor-grab"
                  />
                ))}
              </g>
            )}
          </svg>
        )}

        {!streamReady && (
          <div className="absolute inset-0 z-30 grid place-items-center bg-slate-950/95 text-center">
            <div>
              <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-2 border-slate-700 border-t-cyan-400" />
              <p className="font-medium text-slate-300">{waitingMessage}</p>
              {streamError && <p className="mt-2 max-w-md text-sm text-red-300">{streamError}</p>}
            </div>
          </div>
        )}
      </div>

      {(notice || loadError) && (
        <p role="status" className={`border-t px-4 py-2 text-xs ${(loadError || notice.includes('못') || notice.includes('오류')) ? 'border-red-500/20 bg-red-500/10 text-red-300' : 'border-cyan-500/20 bg-cyan-500/5 text-cyan-300'}`}>
          {loadError || notice}
        </p>
      )}

      {editorOpen && (
        <div className="border-t border-slate-800 bg-slate-950/35 p-4">
          <div className="grid gap-4 lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.2fr)]">
            <div className="space-y-3">
              <label className="block text-xs font-medium text-slate-300">
                구역 이름
                <input value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} maxLength="50" placeholder="예: 2층 출입금지 구역" className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500" />
              </label>
              <div className="grid grid-cols-2 gap-2">
                <label className="block text-xs font-medium text-slate-300">
                  위험 유형
                  <select value={form.zone_type} onChange={(event) => setForm((current) => ({ ...current, zone_type: event.target.value }))} className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500">
                    {Object.entries(ZONE_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
                <label className="block text-xs font-medium text-slate-300">
                  위험 수준
                  <select value={form.risk_level} onChange={(event) => setForm((current) => ({ ...current, risk_level: event.target.value }))} className="mt-1.5 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500">
                    {Object.entries(RISK_LEVELS).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
                  </select>
                </label>
              </div>
              <label className="block text-xs font-medium text-slate-300">
                설명
                <textarea value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} maxLength="1000" rows="2" placeholder="이 구역의 위험 요소를 설명하세요." className="mt-1.5 w-full resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500" />
              </label>
              <label className="block text-xs font-medium text-slate-300">
                주의사항
                <textarea value={form.precautions} onChange={(event) => setForm((current) => ({ ...current, precautions: event.target.value }))} maxLength="1000" rows="2" placeholder="작업자가 지켜야 할 사항을 입력하세요." className="mt-1.5 w-full resize-y rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500" />
              </label>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-white">{editingZoneId ? '위험구역 수정' : '위험구역 생성'}</p>
                  <p className="mt-1 text-xs text-slate-500">선택한 점 {points.length}개 · 좌표는 영상 크기에 비례해 저장됩니다.</p>
                </div>
                <span className={`rounded-full px-2 py-1 text-xs ${RISK_LEVELS[form.risk_level].badge}`}>{RISK_LEVELS[form.risk_level].label}</span>
              </div>

              {phase === 'details' && (
                <button type="button" onClick={beginDrawing} disabled={!form.name.trim()} className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40">그리기 시작</button>
              )}

              {(phase === 'drawing' || phase === 'editing') && (
                <div className="flex flex-wrap gap-2">
                  {phase === 'editing' && (
                    <>
                      <button type="button" onClick={() => setTool('vertices')} className={`rounded-lg border px-3 py-2 text-xs ${tool === 'vertices' ? 'border-cyan-400 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400'}`}>점 수정</button>
                      <button type="button" onClick={() => setTool('move')} className={`rounded-lg border px-3 py-2 text-xs ${tool === 'move' ? 'border-cyan-400 bg-cyan-500/10 text-cyan-300' : 'border-slate-700 text-slate-400'}`}>위치 이동</button>
                    </>
                  )}
                  <button type="button" onClick={undoPointChange} disabled={!history.length} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-cyan-500 disabled:opacity-40">되돌리기</button>
                  <button type="button" onClick={showPreview} disabled={points.length < 3} className="rounded-lg bg-violet-500 px-3 py-2 text-xs font-semibold text-white hover:bg-violet-400 disabled:opacity-40">영역 닫기·미리보기</button>
                </div>
              )}

              {phase === 'preview' && (
                <div className="flex flex-wrap gap-2">
                  <button type="button" onClick={continueEditing} className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:border-cyan-500">계속 수정</button>
                  <button type="button" onClick={saveZone} disabled={saving || !form.name.trim()} className="rounded-lg bg-emerald-500 px-4 py-2 text-xs font-semibold text-slate-950 hover:bg-emerald-400 disabled:cursor-wait disabled:opacity-50">{saving ? '저장 중...' : '위험구역 저장'}</button>
                </div>
              )}

              <button type="button" onClick={resetEditor} className="mt-3 text-xs text-red-300 hover:text-red-200">생성·수정 취소</button>
            </div>
          </div>
        </div>
      )}

      <div className="border-t border-slate-800 px-4 py-3">
        <div className="mb-2 flex items-center justify-between">
          <p className="text-xs font-semibold text-slate-300">이 카메라의 위험구역</p>
          <button type="button" onClick={loadZones} className="text-xs text-slate-500 hover:text-cyan-300">새로고침</button>
        </div>
        {zones.length ? (
          <div className="grid gap-2 md:grid-cols-2">
            {zones.map((zone) => (
              <article key={zone.id} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="truncate text-sm font-semibold text-white">{zone.name}</p>
                      <span className={`rounded-full px-2 py-0.5 text-[10px] ${RISK_LEVELS[zone.risk_level]?.badge || RISK_LEVELS.high.badge}`}>{RISK_LEVELS[zone.risk_level]?.label || '높음'}</span>
                    </div>
                    <p className="mt-1 text-xs text-slate-500">{ZONE_TYPES[zone.zone_type]} · 점 {zone.polygon.length}개</p>
                    {zone.description && <p className="mt-2 line-clamp-2 text-xs text-slate-400">{zone.description}</p>}
                    {zone.precautions && <p className="mt-1 line-clamp-2 text-xs text-amber-300">주의 · {zone.precautions}</p>}
                  </div>
                  <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${zone.visible ? 'bg-emerald-400' : 'bg-slate-600'}`} title={zone.visible ? '표시 중' : '숨김'} />
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" onClick={() => toggleZoneVisibility(zone)} className="rounded-md border border-slate-700 px-2.5 py-1 text-[11px] text-slate-300 hover:border-cyan-500">{zone.visible ? '숨기기' : '표시하기'}</button>
                  <button type="button" onClick={() => beginEdit(zone)} disabled={editorOpen} className="rounded-md border border-cyan-500/40 px-2.5 py-1 text-[11px] text-cyan-300 disabled:opacity-40">모양·정보 수정</button>
                  <button type="button" onClick={() => deleteZone(zone)} className="rounded-md border border-red-500/30 px-2.5 py-1 text-[11px] text-red-300">삭제</button>
                </div>
              </article>
            ))}
          </div>
        ) : !loading && <p className="rounded-lg border border-dashed border-slate-700 px-3 py-5 text-center text-xs text-slate-500">이 카메라에 저장된 위험구역이 없습니다.</p>}
      </div>
    </>
  )
}
