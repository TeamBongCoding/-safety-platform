import { useCallback, useEffect, useRef, useState } from 'react'
import { cameraUploadUrl } from './api'

const FRAME_INTERVAL_MS = 100
const MAX_BUFFERED_BYTES = 64_000
const ACK_TIMEOUT_MS = 1500
const UPLOAD_WIDTH = 320

const CAMERA_PROFILES = [
  { width: { exact: 320 }, height: { exact: 240 }, frameRate: { ideal: 10, max: 10 } },
  { width: { ideal: 320, max: 320 }, height: { ideal: 240, max: 240 }, frameRate: { ideal: 10, max: 10 } },
]

async function openUsbCamera(deviceId) {
  let lastError
  for (const profile of CAMERA_PROFILES) {
    try {
      return await navigator.mediaDevices.getUserMedia({
        video: { deviceId: { exact: deviceId }, ...profile },
        audio: false,
      })
    } catch (error) {
      lastError = error
      if (error?.name === 'NotAllowedError' || error?.name === 'NotReadableError') throw error
    }
  }
  throw lastError
}

export default function BrowserCameraController({
  cameraId = 'camera-1',
  title = '카메라 A',
  preferredDeviceIndex = 0,
  unavailableDeviceIds = [],
  onDeviceSelectionChange,
  onStreamingChange,
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
  const awaitingAckRef = useRef(false)
  const ackTimerRef = useRef(null)

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
    awaitingAckRef.current = false
    frameCounterRef.current = 0
    window.clearTimeout(ackTimerRef.current)
    ackTimerRef.current = null

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
      let all = await navigator.mediaDevices.enumerateDevices()
      let cams = all.filter((d) => d.kind === 'videoinput' && d.deviceId)
      if (!cams.some((camera) => camera.label)) {
        permStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
        permStream.getTracks().forEach((track) => track.stop())
        permStream = null
        all = await navigator.mediaDevices.enumerateDevices()
        cams = all.filter((d) => d.kind === 'videoinput' && d.deviceId)
      }
      setDevices(cams)
      const selectedIsAvailable = cams.some(
        (cam) => cam.deviceId === selectedDeviceId && !unavailableDeviceIds.includes(cam.deviceId),
      )
      if (cams.length && !selectedIsAvailable) {
        const available = cams.filter((cam) => !unavailableDeviceIds.includes(cam.deviceId))
        const preferred = cams[preferredDeviceIndex]
        const next = preferred && !unavailableDeviceIds.includes(preferred.deviceId)
          ? preferred
          : available[0]
        const nextDeviceId = next?.deviceId ?? ''
        setSelectedDeviceId(nextDeviceId)
        onDeviceSelectionChange?.(cameraId, nextDeviceId)
        if (!nextDeviceId) setError('다른 입력에서 사용하지 않는 카메라가 없습니다.')
      }
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
        awaitingAckRef.current ||
        encodingRef.current ||
        video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA
      ) return

      const sw = video.videoWidth || 640
      const sh = video.videoHeight || 480
      const tw = Math.min(sw, UPLOAD_WIDTH)
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
        if (socket.bufferedAmount > MAX_BUFFERED_BYTES) return
        awaitingAckRef.current = true
        socket.send(blob)
        frameCounterRef.current += 1
        window.clearTimeout(ackTimerRef.current)
        ackTimerRef.current = window.setTimeout(() => {
          awaitingAckRef.current = false
          ackTimerRef.current = null
        }, ACK_TIMEOUT_MS)
      }, 'image/jpeg', 0.68)
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
    if (!testVideo && unavailableDeviceIds.includes(selectedDeviceId)) {
      setError('다른 카메라 입력에서 이미 선택한 장치입니다. 다른 번호를 선택하세요.')
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
        const stream = await openUsbCamera(selectedDeviceId)
        streamRef.current = stream
        video.srcObject = stream
      }
      await video.play()

      const socket = new WebSocket(cameraUploadUrl(cameraId))
      socketRef.current = socket
      setStatus('connecting')

      socket.onopen = () => {
        if (socketRef.current !== socket) return
        setStatus('streaming')
        onStreamingChange?.(cameraId, true)
        beginFrameUpload(socket)
      }
      socket.onmessage = (event) => {
        if (socketRef.current !== socket) return
        try {
          const message = JSON.parse(event.data)
          if (message.type !== 'frame_ack') return
          awaitingAckRef.current = false
          window.clearTimeout(ackTimerRef.current)
          ackTimerRef.current = null
        } catch {
          // 카메라 업로드 소켓은 frame_ack JSON만 사용한다.
        }
      }
      socket.onerror = () => {
        if (socketRef.current === socket) setError('서버 연결에 실패했습니다.')
      }
      socket.onclose = (event) => {
        if (socketRef.current !== socket) return
        socketRef.current = null
        window.clearInterval(frameTimerRef.current)
        window.clearInterval(statsTimerRef.current)
        window.clearTimeout(ackTimerRef.current)
        ackTimerRef.current = null
        awaitingAckRef.current = false
        if (streamRef.current) { streamRef.current.getTracks().forEach((t) => t.stop()); streamRef.current = null }
        if (videoRef.current) videoRef.current.srcObject = null
        if (objectUrlRef.current) { URL.revokeObjectURL(objectUrlRef.current); objectUrlRef.current = null }
        setUploadFps(0)
        onStreamingChange?.(cameraId, false)
        const next = event.code === 1000 ? 'idle' : 'error'
        setStatus(next)
        if (event.code !== 1000) setError(event.reason || '카메라 연결이 종료되었습니다.')
      }
    } catch (err) {
      releaseResources()
      setStatus('error')
      onStreamingChange?.(cameraId, false)
      if (err?.name === 'NotAllowedError') setError('카메라 권한이 거부되었습니다.')
      else if (err?.name === 'NotFoundError' || err?.name === 'OverconstrainedError') setError('선택한 카메라를 찾을 수 없습니다. 카메라를 다시 검색하세요.')
      else if (err?.name === 'NotReadableError') setError('USB 카메라를 열 수 없습니다. 다른 탭·카메라 앱을 모두 닫고 USB 포트를 분리해서 연결하세요.')
      else setError(err?.message || '카메라를 시작하지 못했습니다.')
    }
  }

  const stopCamera = () => {
    releaseResources()
    setStatus('idle')
    setUploadFps(0)
    setError('')
    onStreamingChange?.(cameraId, false)
  }

  const isBusy = status === 'requesting' || status === 'connecting'
  const isStreaming = status === 'streaming'
  const selectedDeviceIndex = devices.findIndex((d) => d.deviceId === selectedDeviceId)
  const selectedDevice = devices[selectedDeviceIndex]
  const deviceLabel = testVideo?.name
    || (selectedDevice
      ? (selectedDevice.label || 'USB 카메라') + ' · ' + (selectedDeviceIndex + 1)
      : '카메라')

  return (
    <section className="mb-5 rounded-2xl border border-cyan-500/20 bg-cyan-500/5 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="font-semibold text-white">{title} / 영상 입력</h2>
          <p className="text-xs text-slate-500">{cameraId}에 연결할 USB 카메라 또는 영상 파일을 선택합니다.</p>
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
            onChange={(e) => {
              setSelectedDeviceId(e.target.value)
              setTestVideo(null)
              onDeviceSelectionChange?.(cameraId, e.target.value)
            }}
            disabled={isStreaming || isBusy}
            className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500 disabled:opacity-60"
          >
            <option value="">카메라 장치 선택 (검색 먼저)</option>
            {devices.map((d, i) => (
              <option
                key={d.deviceId}
                value={d.deviceId}
                disabled={unavailableDeviceIds.includes(d.deviceId)}
              >
                {d.label || 'USB 카메라'} · {i + 1}
                {unavailableDeviceIds.includes(d.deviceId) ? ' (다른 입력에서 사용 중)' : ''}
              </option>
            ))}
          </select>

          <label className="cursor-pointer rounded-lg border border-dashed border-slate-700 px-3 py-2 text-xs text-slate-400 hover:border-slate-500">
            또는 영상 파일 선택 (녹화 영상)
            <input
              type="file"
              accept="video/*"
              disabled={isStreaming || isBusy}
              onChange={(e) => {
                setTestVideo(e.target.files?.[0] ?? null)
                setSelectedDeviceId('')
                onDeviceSelectionChange?.(cameraId, '')
              }}
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
