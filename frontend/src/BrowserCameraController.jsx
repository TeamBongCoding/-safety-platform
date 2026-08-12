import { useCallback, useEffect, useRef, useState } from 'react'
import { WS_URL } from './api'

const FRAME_INTERVAL_MS = 100
const MAX_BUFFERED_BYTES = 500_000

function cameraUploadWsUrl() {
  return WS_URL.replace(/\/ws\/?$/, '') + '/ws/camera-upload'
}

export default function BrowserCameraController({ onStreamingChange }) {
  const videoRef = useRef(null)
  const canvasRef = useRef(null)
  const streamRef = useRef(null)
  const socketRef = useRef(null)
  const frameTimerRef = useRef(null)
  const statsTimerRef = useRef(null)
  const frameCounterRef = useRef(0)
  const encodingRef = useRef(false)
  const objectUrlRef = useRef(null)

  const [devices, setDevices] = useState([])
  const [selectedDeviceId, setSelectedDeviceId] = useState('')
  const [testVideo, setTestVideo] = useState(null)
  const [flipHorizontal, setFlipHorizontal] = useState(true)
  const [status, setStatus] = useState('idle')
  const [uploadFps, setUploadFps] = useState(0)
  const [error, setError] = useState('')
  const [discovering, setDiscovering] = useState(false)

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
        socket.close(1000, 'stopped')
      }
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
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

  const discoverCameras = async () => {
    if (!navigator.mediaDevices?.enumerateDevices) {
      setError('이 주소에서는 카메라를 검색할 수 없습니다. HTTPS 또는 localhost로 접속하세요.')
      return
    }
    setDiscovering(true)
    setError('')
    let permStream = null
    try {
      permStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
      permStream.getTracks().forEach((t) => t.stop())
      const all = await navigator.mediaDevices.enumerateDevices()
      const cams = all.filter((d) => d.kind === 'videoinput' && d.deviceId)
      setDevices(cams)
      if (cams.length && !selectedDeviceId) setSelectedDeviceId(cams[0].deviceId)
      if (!cams.length) setError('연결된 카메라를 찾지 못했습니다.')
    } catch (err) {
      if (permStream) permStream.getTracks().forEach((t) => t.stop())
      setError(err?.name === 'NotAllowedError'
        ? '카메라 권한이 거부되었습니다. 브라우저 주소창에서 권한을 허용하세요.'
        : err?.message || '카메라를 검색하지 못했습니다.')
    } finally {
      setDiscovering(false)
    }
  }

  const beginFrameUpload = useCallback((socket) => {
    const video = videoRef.current
    const canvas = canvasRef.current
    const ctx = canvas.getContext('2d', { alpha: false })

    frameTimerRef.current = window.setInterval(() => {
      if (
        socket.readyState !== WebSocket.OPEN ||
        socket.bufferedAmount > MAX_BUFFERED_BYTES ||
        encodingRef.current ||
        video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      ) return

      const sw = video.videoWidth || 640
      const sh = video.videoHeight || 480
      const tw = Math.min(sw, 640)
      const th = Math.round(sh * (tw / sw))
      if (canvas.width !== tw || canvas.height !== th) { canvas.width = tw; canvas.height = th }
      ctx.save()
      if (flipHorizontal) { ctx.translate(tw, 0); ctx.scale(-1, 1) }
      ctx.drawImage(video, 0, 0, tw, th)
      ctx.restore()
      encodingRef.current = true
      canvas.toBlob((blob) => {
        encodingRef.current = false
        if (!blob || socket.readyState !== WebSocket.OPEN) return
        socket.send(blob)
        frameCounterRef.current += 1
      }, 'image/jpeg', 0.7)
    }, FRAME_INTERVAL_MS)

    statsTimerRef.current = window.setInterval(() => {
      setUploadFps(frameCounterRef.current)
      frameCounterRef.current = 0
    }, 1000)
  }, [flipHorizontal])

  const startCamera = async () => {
    if (!selectedDeviceId && !testVideo) {
      setError('카메라를 선택하거나 영상 파일을 추가하세요.')
      return
    }
    if (!testVideo && !navigator.mediaDevices?.getUserMedia) {
      setError('이 주소에서는 카메라를 사용할 수 없습니다. HTTPS 또는 localhost로 접속하세요.')
      return
    }
    setError('')
    setStatus('requesting')

    try {
      const video = videoRef.current
      if (testVideo) {
        objectUrlRef.current = URL.createObjectURL(testVideo)
        video.src = objectUrlRef.current
        video.loop = true
      } else {
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { deviceId: { exact: selectedDeviceId }, width: { ideal: 640 }, height: { ideal: 480 } },
          audio: false,
        })
        streamRef.current = stream
        video.srcObject = stream
      }
      await video.play()

      const socket = new WebSocket(cameraUploadWsUrl())
      socketRef.current = socket
      setStatus('connecting')

      socket.onopen = () => {
        if (socketRef.current !== socket) return
        setStatus('streaming')
        onStreamingChange?.(true)
        beginFrameUpload(socket)
      }
      socket.onerror = () => {
        if (socketRef.current === socket) setError('서버 연결에 실패했습니다.')
      }
      socket.onclose = (event) => {
        if (socketRef.current !== socket) return
        socketRef.current = null
        window.clearInterval(frameTimerRef.current)
        window.clearInterval(statsTimerRef.current)
        if (streamRef.current) { streamRef.current.getTracks().forEach((t) => t.stop()); streamRef.current = null }
        if (videoRef.current) videoRef.current.srcObject = null
        if (objectUrlRef.current) { URL.revokeObjectURL(objectUrlRef.current); objectUrlRef.current = null }
        setUploadFps(0)
        onStreamingChange?.(false)
        const next = event.code === 1000 ? 'idle' : 'error'
        setStatus(next)
        if (event.code !== 1000) setError(event.reason || '카메라 연결이 종료되었습니다.')
      }
    } catch (err) {
      releaseResources()
      setStatus('error')
      onStreamingChange?.(false)
      if (err?.name === 'NotAllowedError') setError('카메라 권한이 거부되었습니다.')
      else if (err?.name === 'NotFoundError' || err?.name === 'OverconstrainedError') setError('선택한 카메라를 찾을 수 없습니다. 카메라를 다시 검색하세요.')
      else if (err?.name === 'NotReadableError') setError('카메라를 열 수 없습니다. 다른 앱에서 사용 중인지 확인하세요.')
      else setError(err?.message || '카메라를 시작하지 못했습니다.')
    }
  }

  const stopCamera = () => {
    releaseResources()
    setStatus('idle')
    setUploadFps(0)
    setError('')
    onStreamingChange?.(false)
  }

  const isBusy = status === 'requesting' || status === 'connecting'
  const isStreaming = status === 'streaming'
  const deviceLabel = testVideo?.name
    || devices.find((d) => d.deviceId === selectedDeviceId)?.label
    || '카메라'

  return (
    <section className="mb-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/5 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold text-white">카메라 / 영상 입력</h2>
          <p className="text-xs text-slate-500">클라이언트 카메라 또는 영상 파일을 선택해 AI 분석합니다.</p>
        </div>
        {!isStreaming && (
          <button
            onClick={discoverCameras}
            disabled={discovering || isBusy}
            className="rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-4 py-2 text-sm font-semibold text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {discovering ? '검색 중...' : devices.length ? '카메라 다시 검색' : '카메라 검색'}
          </button>
        )}
      </div>

      <div className="grid gap-4 sm:grid-cols-[160px_1fr]">
        {/* 로컬 미리보기 */}
        <div className="relative aspect-video overflow-hidden rounded-lg border border-slate-800 bg-black">
          <video
            ref={videoRef}
            muted
            playsInline
            className={`h-full w-full object-cover ${flipHorizontal ? '-scale-x-100' : ''}`}
          />
          {!isStreaming && (
            <div className="absolute inset-0 grid place-items-center text-xs text-slate-600">미리보기</div>
          )}
          {isStreaming && (
            <span className="absolute left-2 top-2 rounded bg-red-500 px-1.5 py-0.5 text-[10px] font-bold text-white">LIVE</span>
          )}
        </div>

        {/* 설정 */}
        <div className="flex flex-col gap-2">
          <select
            value={selectedDeviceId}
            onChange={(e) => { setSelectedDeviceId(e.target.value); setTestVideo(null) }}
            disabled={isStreaming || isBusy}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500 disabled:opacity-60"
          >
            <option value="">카메라 장치 선택 (검색 먼저)</option>
            {devices.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `카메라 ${i + 1}`}
              </option>
            ))}
          </select>

          <label className="cursor-pointer rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-400 hover:border-slate-500">
            또는 영상 파일 선택 (녹화 영상)
            <input
              type="file"
              accept="video/*"
              disabled={isStreaming || isBusy}
              onChange={(e) => { setTestVideo(e.target.files?.[0] ?? null); setSelectedDeviceId('') }}
              className="mt-1 block w-full text-xs file:mr-2 file:rounded file:border-0 file:bg-cyan-500/15 file:px-2 file:py-1 file:text-cyan-300"
            />
          </label>

          {!isStreaming && (
            <label className="flex items-center gap-2 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={flipHorizontal}
                disabled={isBusy}
                onChange={(e) => setFlipHorizontal(e.target.checked)}
                className="h-4 w-4 accent-cyan-500"
              />
              좌우 반전 보정 (웹캠 거울 모드 해제)
            </label>
          )}

          <div className="flex gap-2 pt-1">
            {isStreaming ? (
              <button
                onClick={stopCamera}
                className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm font-semibold text-red-300 hover:bg-red-500/20"
              >
                분석 중지
              </button>
            ) : (
              <button
                onClick={startCamera}
                disabled={isBusy || (!selectedDeviceId && !testVideo)}
                className="rounded-lg bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isBusy ? '연결 중...' : testVideo ? '영상 파일 분석 시작' : '카메라 분석 시작'}
              </button>
            )}
          </div>

          {isStreaming && (
            <p className="text-xs text-emerald-400">
              {deviceLabel} · 서버 전송 {uploadFps} FPS · 분석 결과는 우측 영상에서 확인하세요
            </p>
          )}
          {!isStreaming && !devices.length && !testVideo && (
            <p className="text-xs text-slate-600">카메라 검색 버튼을 눌러 연결된 카메라를 찾거나, 영상 파일을 선택하세요.</p>
          )}
        </div>
      </div>

      {error && (
        <p role="alert" className="mt-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {error}
        </p>
      )}
      <canvas ref={canvasRef} className="hidden" aria-hidden="true" />
    </section>
  )
}
