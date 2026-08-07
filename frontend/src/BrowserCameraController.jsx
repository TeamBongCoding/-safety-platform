import { useCallback, useEffect, useRef, useState } from 'react'
import { api, cameraUploadUrl } from './api'

const CAMERA_LIMIT = 2
const FRAME_INTERVAL_MS = 125
const MAX_BUFFERED_BYTES = 1_000_000
const EMPTY_SLOT = { cameraId: null, name: '', status: 'idle', uploadFps: 0 }

function CameraSlot({
  index,
  site,
  devices,
  selectedDeviceId,
  otherSelectedDeviceId,
  selectedForMonitoring,
  onDeviceChange,
  onMonitor,
  onStateChange,
}) {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const socketRef = useRef(null)
  const frameTimerRef = useRef(null)
  const statsTimerRef = useRef(null)
  const frameCounterRef = useRef(0)
  const encodingRef = useRef(false)
  const objectUrlRef = useRef(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState('')
  const [uploadFps, setUploadFps] = useState(0)
  const [testVideo, setTestVideo] = useState(null)
  const [flipHorizontal, setFlipHorizontal] = useState(true)

  const device = devices.find((item) => item.deviceId === selectedDeviceId)
  const deviceName = testVideo?.name || device?.label || `카메라 ${index + 1}`

  const releaseResources = useCallback(() => {
    window.clearInterval(frameTimerRef.current)
    window.clearInterval(statsTimerRef.current)
    frameTimerRef.current = null
    statsTimerRef.current = null
    encodingRef.current = false
    frameCounterRef.current = 0

    if (socketRef.current) {
      const socket = socketRef.current
      socketRef.current = null
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
        socket.close(1000, 'camera stopped')
      }
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => track.stop())
      streamRef.current = null
    }
    if (videoRef.current) videoRef.current.srcObject = null
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current)
      objectUrlRef.current = null
    }
    if (videoRef.current) videoRef.current.removeAttribute('src')
  }, [])

  useEffect(() => () => releaseResources(), [releaseResources])

  const reportState = (nextStatus, cameraId = null, fps = 0, name = deviceName) => {
    onStateChange(index, { cameraId, name, status: nextStatus, uploadFps: fps })
  }

  const findOrCreateCamera = async () => {
    const sourceKey = testVideo ? `test-video-${index}` : selectedDeviceId
    const storageKey = `safety_browser_camera_${site.id}_${sourceKey}`
    let savedId = null
    try {
      savedId = Number(window.localStorage.getItem(storageKey)) || null
    } catch {
      // 로컬 설정 저장에 실패해도 서버 카메라 등록은 계속한다.
    }

    const cameras = await api('/api/cameras')
    let camera = cameras.find((item) => item.id === savedId && item.source === 'browser')
    if (!camera) {
      camera = await api('/api/cameras', {
        method: 'POST',
        body: JSON.stringify({ name: testVideo ? `테스트 영상 ${index + 1}` : deviceName, source: 'browser' }),
      })
      try {
        window.localStorage.setItem(storageKey, String(camera.id))
      } catch {
        // 카메라 ID 저장은 편의 기능이므로 전송 실패로 처리하지 않는다.
      }
    }
    return camera
  }

  const beginFrameUpload = (socket, camera) => {
    const video = videoRef.current
    const canvas = canvasRef.current
    const context = canvas.getContext('2d', { alpha: false })

    frameTimerRef.current = window.setInterval(() => {
      if (
        socket.readyState !== WebSocket.OPEN ||
        socket.bufferedAmount > MAX_BUFFERED_BYTES ||
        encodingRef.current ||
        video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      ) return

      const sourceWidth = video.videoWidth || 640
      const sourceHeight = video.videoHeight || 480
      const targetWidth = Math.min(sourceWidth, 640)
      const targetHeight = Math.round(sourceHeight * (targetWidth / sourceWidth))
      if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
        canvas.width = targetWidth
        canvas.height = targetHeight
      }
      context.save()
      if (flipHorizontal) {
        context.translate(targetWidth, 0)
        context.scale(-1, 1)
      }
      context.drawImage(video, 0, 0, targetWidth, targetHeight)
      context.restore()
      encodingRef.current = true
      canvas.toBlob((blob) => {
        encodingRef.current = false
        if (!blob || socket.readyState !== WebSocket.OPEN) return
        socket.send(blob)
        frameCounterRef.current += 1
      }, 'image/jpeg', 0.7)
    }, FRAME_INTERVAL_MS)

    statsTimerRef.current = window.setInterval(() => {
      const fps = frameCounterRef.current
      frameCounterRef.current = 0
      setUploadFps(fps)
      reportState('streaming', camera.id, fps, camera.name)
    }, 1000)
  }

  const stopCamera = () => {
    releaseResources()
    setStatus('idle')
    setUploadFps(0)
    setError('')
    reportState('idle')
  }

  const startCamera = async () => {
    setError('')
    if (!selectedDeviceId && !testVideo) {
      setError('카메라를 선택하거나 테스트 영상을 추가하세요.')
      return
    }
    if (!testVideo && selectedDeviceId === otherSelectedDeviceId) {
      setError('같은 카메라를 두 슬롯에서 동시에 사용할 수 없습니다.')
      return
    }
    if (!testVideo && !navigator.mediaDevices?.getUserMedia) {
      setError('이 주소에서는 카메라를 사용할 수 없습니다. HTTPS 또는 localhost로 접속하세요.')
      return
    }

    setStatus('requesting')
    reportState('requesting')
    try {
      const video = videoRef.current
      if (testVideo) {
        objectUrlRef.current = URL.createObjectURL(testVideo)
        video.src = objectUrlRef.current
        video.loop = true
      } else {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: {
            deviceId: { exact: selectedDeviceId },
            width: { ideal: 640 },
            height: { ideal: 480 },
          },
          audio: false,
        })
        streamRef.current = stream
        video.srcObject = stream
      }
      await video.play()

      const camera = await findOrCreateCamera()
      const socket = new WebSocket(cameraUploadUrl(camera.id))
      socketRef.current = socket
      setStatus('connecting')
      reportState('connecting', camera.id, 0, camera.name)

      socket.onopen = () => {
        if (socketRef.current !== socket) return
        setStatus('streaming')
        reportState('streaming', camera.id, 0, camera.name)
        beginFrameUpload(socket, camera)
      }
      socket.onerror = () => {
        if (socketRef.current === socket) setError('카메라 서버 연결에 실패했습니다.')
      }
      socket.onclose = (event) => {
        if (socketRef.current !== socket) return
        socketRef.current = null
        window.clearInterval(frameTimerRef.current)
        window.clearInterval(statsTimerRef.current)
        frameTimerRef.current = null
        statsTimerRef.current = null
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop())
          streamRef.current = null
        }
        if (videoRef.current) videoRef.current.srcObject = null
        if (objectUrlRef.current) {
          URL.revokeObjectURL(objectUrlRef.current)
          objectUrlRef.current = null
        }
        setUploadFps(0)
        const nextStatus = event.code === 1000 ? 'idle' : 'error'
        setStatus(nextStatus)
        reportState(nextStatus)
        if (event.code !== 1000) setError(event.reason || '카메라 전송 연결이 종료되었습니다.')
      }
    } catch (cameraError) {
      releaseResources()
      setStatus('error')
      setUploadFps(0)
      reportState('error')
      if (cameraError?.name === 'NotAllowedError') {
        setError('카메라 권한이 거부되었습니다. 브라우저 주소창에서 권한을 허용하세요.')
      } else if (cameraError?.name === 'NotFoundError' || cameraError?.name === 'OverconstrainedError') {
        setError('선택한 카메라를 찾을 수 없습니다. 카메라를 다시 검색하세요.')
      } else if (cameraError?.name === 'NotReadableError') {
        setError('카메라를 열 수 없습니다. 다른 앱에서 사용 중인지 확인하세요.')
      } else {
        setError(cameraError?.message || '카메라를 시작하지 못했습니다.')
      }
    }
  }

  const isBusy = status === 'requesting' || status === 'connecting'
  const isStreaming = status === 'streaming'

  return (
    <article className={`rounded-xl border p-3 ${selectedForMonitoring ? 'border-cyan-400 bg-cyan-500/10' : 'border-slate-700 bg-slate-950/50'}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-white">카메라 {index + 1}</h3>
        {selectedForMonitoring && <span className="rounded-full bg-cyan-400/15 px-2 py-1 text-[11px] font-semibold text-cyan-300">관제 화면 선택됨</span>}
      </div>

      <div className="grid gap-3 sm:grid-cols-[160px_minmax(0,1fr)]">
        <div className="relative aspect-video overflow-hidden rounded-lg border border-slate-800 bg-black">
          <video ref={videoRef} muted playsInline className={`h-full w-full object-cover ${flipHorizontal ? '-scale-x-100' : ''}`} />
          {!isStreaming && <div className="absolute inset-0 grid place-items-center text-center text-xs text-slate-500">카메라 대기</div>}
          {isStreaming && <span className="absolute left-2 top-2 rounded bg-red-500 px-1.5 py-0.5 text-[10px] font-bold text-white">LIVE</span>}
        </div>

        <div className="flex min-w-0 flex-col gap-2">
          <select
            value={selectedDeviceId}
            onChange={(event) => onDeviceChange(index, event.target.value)}
            disabled={isStreaming || isBusy}
            aria-label={`카메라 ${index + 1} 장치 선택`}
            className="min-w-0 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500 disabled:opacity-60"
          >
            <option value="">장치를 선택하세요</option>
            {devices.map((item, deviceIndex) => (
              <option key={item.deviceId} value={item.deviceId} disabled={item.deviceId === otherSelectedDeviceId}>
                {item.label || `카메라 장치 ${deviceIndex + 1}`}
              </option>
            ))}
          </select>

          <label className="rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-400">
            또는 테스트 영상
            <input
              type="file"
              accept="video/*"
              disabled={isStreaming || isBusy}
              onChange={(event) => setTestVideo(event.target.files?.[0] ?? null)}
              className="mt-1 block w-full text-xs file:mr-2 file:rounded file:border-0 file:bg-cyan-500/15 file:px-2 file:py-1 file:text-cyan-300"
            />
          </label>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={flipHorizontal}
              disabled={isStreaming || isBusy}
              onChange={(event) => setFlipHorizontal(event.target.checked)}
              className="h-4 w-4 accent-cyan-500"
            />
            좌우 반전 보정
          </label>

          <div className="flex flex-wrap gap-2">
            {isStreaming ? (
              <>
                <button onClick={stopCamera} className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs font-semibold text-red-300 hover:bg-red-500/20">중지</button>
                <button onClick={() => onMonitor(index)} disabled={selectedForMonitoring} className="rounded-lg border border-cyan-500/40 px-3 py-2 text-xs font-semibold text-cyan-300 hover:bg-cyan-500/10 disabled:cursor-default disabled:opacity-50">관제 화면에서 보기</button>
              </>
            ) : (
              <button onClick={startCamera} disabled={isBusy || (!selectedDeviceId && !testVideo) || (!testVideo && selectedDeviceId === otherSelectedDeviceId)} className="rounded-lg bg-cyan-500 px-3 py-2 text-xs font-semibold text-slate-950 hover:bg-cyan-400 disabled:cursor-not-allowed disabled:opacity-40">
                {isBusy ? '연결 중...' : testVideo ? '테스트 영상 시작' : '카메라 시작'}
              </button>
            )}
          </div>

          <p className="text-xs text-slate-400">
            {isStreaming ? `${deviceName} · 서버 전송 ${uploadFps} FPS` : testVideo ? `테스트 영상 · ${testVideo.name}` : deviceName}
          </p>
        </div>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</p>}
      <canvas ref={canvasRef} className="hidden" aria-hidden="true" />
    </article>
  )
}

export default function BrowserCameraController({ site, onCameraChange }) {
  const [devices, setDevices] = useState([])
  const [deviceSelections, setDeviceSelections] = useState(['', ''])
  const [slotStates, setSlotStates] = useState(() => Array.from({ length: CAMERA_LIMIT }, () => ({ ...EMPTY_SLOT })))
  const [selectedCameraId, setSelectedCameraId] = useState(null)
  const [discovering, setDiscovering] = useState(false)
  const [discoveryError, setDiscoveryError] = useState('')
  const slotStatesRef = useRef(slotStates)
  const selectedCameraIdRef = useRef(null)

  const chooseMonitoringCamera = useCallback((cameraId) => {
    selectedCameraIdRef.current = cameraId
    setSelectedCameraId(cameraId)
    onCameraChange(cameraId)
  }, [onCameraChange])

  const handleSlotStateChange = useCallback((index, nextState) => {
    const previous = slotStatesRef.current[index]
    const next = slotStatesRef.current.map((item, itemIndex) => itemIndex === index ? nextState : item)
    slotStatesRef.current = next
    setSlotStates(next)

    if (nextState.status === 'streaming' && nextState.cameraId && !selectedCameraIdRef.current) {
      chooseMonitoringCamera(nextState.cameraId)
    } else if (
      previous.cameraId &&
      selectedCameraIdRef.current === previous.cameraId &&
      nextState.status !== 'streaming'
    ) {
      const replacement = next.find((item) => item.status === 'streaming' && item.cameraId)
      chooseMonitoringCamera(replacement?.cameraId ?? null)
    }
  }, [chooseMonitoringCamera])

  const handleMonitor = (index) => {
    const slot = slotStatesRef.current[index]
    if (slot.status === 'streaming' && slot.cameraId) chooseMonitoringCamera(slot.cameraId)
  }

  const handleDeviceChange = (index, deviceId) => {
    setDeviceSelections((current) => current.map((item, itemIndex) => itemIndex === index ? deviceId : item))
  }

  const discoverCameras = async () => {
    setDiscoveryError('')
    if (!navigator.mediaDevices?.getUserMedia || !navigator.mediaDevices?.enumerateDevices) {
      setDiscoveryError('이 주소에서는 카메라를 검색할 수 없습니다. HTTPS 또는 localhost로 접속하세요.')
      return
    }

    setDiscovering(true)
    let permissionStream = null
    try {
      permissionStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
      permissionStream.getTracks().forEach((track) => track.stop())
      permissionStream = null

      const mediaDevices = await navigator.mediaDevices.enumerateDevices()
      const videoDevices = mediaDevices.filter((item) => item.kind === 'videoinput' && item.deviceId)
      setDevices(videoDevices)
      setDeviceSelections((current) => {
        const availableIds = new Set(videoDevices.map((item) => item.deviceId))
        const next = ['', '']
        const used = new Set()

        current.forEach((deviceId, index) => {
          if (availableIds.has(deviceId) && !used.has(deviceId)) {
            next[index] = deviceId
            used.add(deviceId)
          }
        })
        next.forEach((deviceId, index) => {
          if (deviceId) return
          const available = videoDevices.find((item) => !used.has(item.deviceId))
          if (available) {
            next[index] = available.deviceId
            used.add(available.deviceId)
          }
        })
        return next
      })
      if (!videoDevices.length) setDiscoveryError('연결된 카메라를 찾지 못했습니다.')
    } catch (cameraError) {
      if (permissionStream) permissionStream.getTracks().forEach((track) => track.stop())
      if (cameraError?.name === 'NotAllowedError') {
        setDiscoveryError('카메라 권한이 거부되었습니다. 브라우저 주소창에서 권한을 허용하세요.')
      } else {
        setDiscoveryError(cameraError?.message || '카메라를 검색하지 못했습니다.')
      }
    } finally {
      setDiscovering(false)
    }
  }

  return (
    <section className="mb-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/5 p-4">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold text-white">카메라 · 테스트 영상 입력</h2>
            <span className="rounded-full bg-slate-800 px-2 py-1 text-xs text-slate-300">최대 2대</span>
            <span className="text-xs text-emerald-300">전송 중 {slotStates.filter((item) => item.status === 'streaming').length}대</span>
          </div>
          <p className="mt-1 text-sm text-slate-400">카메라 2대, 테스트 영상 2개 또는 혼합 입력을 동시에 분석하고 관제 화면을 선택합니다.</p>
          {!window.isSecureContext && window.location.hostname !== 'localhost' && (
            <p className="mt-1 text-xs text-amber-300">원격 기기 카메라는 HTTPS 주소에서만 사용할 수 있습니다.</p>
          )}
        </div>
        <button onClick={discoverCameras} disabled={discovering || slotStates.some((item) => item.status === 'requesting' || item.status === 'connecting' || item.status === 'streaming')} className="shrink-0 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-4 py-2 text-sm font-semibold text-cyan-300 hover:bg-cyan-500/20 disabled:cursor-not-allowed disabled:opacity-50">
          {discovering ? '검색 중...' : slotStates.some((item) => item.status === 'streaming') ? '중지 후 다시 검색' : devices.length ? '카메라 다시 검색' : '카메라 검색'}
        </button>
      </div>

      <div className="grid gap-3 xl:grid-cols-2">
          {Array.from({ length: CAMERA_LIMIT }, (_, index) => (
            <CameraSlot
              key={index}
              index={index}
              site={site}
              devices={devices}
              selectedDeviceId={deviceSelections[index]}
              otherSelectedDeviceId={deviceSelections[index === 0 ? 1 : 0]}
              selectedForMonitoring={Boolean(slotStates[index].cameraId && slotStates[index].cameraId === selectedCameraId)}
              onDeviceChange={handleDeviceChange}
              onMonitor={handleMonitor}
              onStateChange={handleSlotStateChange}
            />
          ))}
      </div>
      {discoveryError && <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">{discoveryError}</p>}
    </section>
  )
}
