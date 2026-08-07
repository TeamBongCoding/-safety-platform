# Cloudflare Pages 배포 설정

이 프로젝트는 Cloudflare Pages에 `frontend`만 배포하고, FastAPI/YOLO 백엔드는 별도 서버에서 실행한다.

## Pages 빌드 설정

| 항목 | 값 |
|---|---|
| Root directory | `frontend` |
| Framework preset | `Vite` |
| Build command | `npm run build` |
| Build output directory | `dist` |
| Node.js | `22.12.0` 이상 |

Pages의 Production/Preview 환경변수에 다음 값을 등록한다.

```dotenv
VITE_API_BASE=https://api.example.com
VITE_WS_BASE=wss://api.example.com/ws
```

## 백엔드 운영 환경변수

같은 상위 도메인을 사용하는 권장 구성:

```dotenv
CORS_ORIGINS=https://app.example.com
COOKIE_SECURE=1
COOKIE_SAMESITE=lax
```

`pages.dev`와 상위 도메인이 완전히 다른 API를 연결하는 구성:

```dotenv
CORS_ORIGINS=https://safety-platform.pages.dev
COOKIE_SECURE=1
COOKIE_SAMESITE=none
```

`CORS_ORIGINS`에는 실제 Pages 주소만 쉼표로 구분해 등록한다. 인증 쿠키를 사용하므로 `*`는 사용하지 않는다.

## 배포 확인

1. `https://api.example.com/health`가 `{"status":"ok"}`를 반환하는지 확인한다.
2. Pages에서 회원가입 또는 로그인한다.
3. 브라우저 개발자 도구에서 `/api/auth/me`가 200을 반환하는지 확인한다.
4. 실시간 상태 WebSocket과 브라우저 카메라 업로드 WebSocket 연결을 확인한다.
