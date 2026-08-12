"""기상청 단기예보 API 기반 현장 체감온도 서비스."""

import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

HeatLevel = Literal["inactive", "caution", "warning", "severe"]

CAUTION_TEMP = 31.0
WARNING_TEMP = 33.0
SEVERE_TEMP = 38.0


def _heat_level(apparent_temp: float) -> HeatLevel:
    if apparent_temp >= SEVERE_TEMP:
        return "severe"
    if apparent_temp >= WARNING_TEMP:
        return "warning"
    if apparent_temp >= CAUTION_TEMP:
        return "caution"
    return "inactive"


def latlon_to_kma_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도를 기상청 격자 좌표 (nx, ny)로 변환 (Lambert Conformal Conic)."""
    RE, GRID = 6371.00877, 5.0
    SLAT1, SLAT2, OLON, OLAT = 30.0, 60.0, 126.0, 38.0
    XO, YO = 210 / GRID, 675 / GRID
    DEG = math.pi / 180.0

    re = RE / GRID
    slat1, slat2 = SLAT1 * DEG, SLAT2 * DEG
    olon, olat = OLON * DEG, OLAT * DEG

    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    )
    sf = (math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn) * math.cos(slat1) / sn
    ro = re * sf / (math.tan(math.pi * 0.25 + olat * 0.5) ** sn)

    ra = re * sf / (math.tan(math.pi * 0.25 + lat * DEG * 0.5) ** sn)
    theta = lon * DEG - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = ra * math.sin(theta) + XO
    y = ro - ra * math.cos(theta) + YO
    return round(x), round(y)


def calc_apparent_temp(t: float, rh: float, ws: float = 0.0) -> float:
    """Steadman (1994) 체감온도."""
    e = rh / 100.0 * 6.105 * math.exp(17.27 * t / (237.7 + t))
    return t + 0.33 * e - 0.70 * ws - 4.00


def _kma_base_time(now: datetime) -> tuple[str, str]:
    """현재 시각에 사용 가능한 최신 단기예보 base_date/base_time 반환."""
    HOURS = [2, 5, 8, 11, 14, 17, 20, 23]
    d = now.date()
    h, m = now.hour, now.minute
    valid_hour = next(
        (hour for hour in reversed(HOURS) if h > hour or (h == hour and m >= 10)),
        None,
    )
    if valid_hour is None:
        d -= timedelta(days=1)
        valid_hour = 23
    return d.strftime('%Y%m%d'), f'{valid_hour:02d}00'


@dataclass
class HeatStatus:
    apparent_temp: float | None
    level: HeatLevel
    stale: bool
    demo_mode: bool
    sun_threshold: float
    shade_threshold: float
    caution_temp: float
    warning_temp: float
    severe_temp: float


class HeatService:
    """현장 1개의 체감온도 상태를 관리한다. 10분마다 KMA API를 폴링한다."""

    POLL_INTERVAL = 600  # seconds

    def __init__(self, lat: float | None, lon: float | None, api_key: str | None):
        self._lat = lat
        self._lon = lon
        self._api_key = api_key
        self._lock = threading.Lock()
        self._apparent_temp: float | None = None
        self._last_fetched: float = 0.0
        self._stale = False
        self._stop = threading.Event()
        self._demo_temp: float | None = None
        self._sun_threshold = 1.15
        self._shade_threshold = 0.85
        self._caution_temp = CAUTION_TEMP
        self._warning_temp = WARNING_TEMP
        self._severe_temp = SEVERE_TEMP

        if api_key and lat is not None and lon is not None:
            t = threading.Thread(target=self._poll_loop, name="heat-poll", daemon=True)
            t.start()

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                self._fetch()
            except Exception as exc:
                logger.warning("기상청 API 호출 실패: %s", exc)
                with self._lock:
                    self._stale = True
            self._stop.wait(self.POLL_INTERVAL)

    def _fetch(self):
        now = datetime.now()
        base_date, base_time = _kma_base_time(now)
        nx, ny = latlon_to_kma_grid(self._lat, self._lon)
        params = urllib.parse.urlencode({
            "pageNo": "1",
            "numOfRows": "300",
            "dataType": "JSON",
            "base_date": base_date,
            "base_time": base_time,
            "nx": str(nx),
            "ny": str(ny),
        })
        url = (
            "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
            f"?serviceKey={self._api_key}&{params}"
        )
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)

        items = data["response"]["body"]["items"]["item"]
        target_date = now.strftime('%Y%m%d')

        # 현재 시각 기준 가장 가까운 예보 슬롯을 찾는다 (최대 3시간 앞까지)
        values: dict[str, float] = {}
        for delta_h in range(4):
            target_time = (
                (now.replace(minute=0, second=0) + timedelta(hours=delta_h))
                .strftime('%H') + '00'
            )
            values = {}
            for item in items:
                if item["fcstDate"] == target_date and item["fcstTime"] == target_time:
                    try:
                        values[item["category"]] = float(item["fcstValue"])
                    except (ValueError, TypeError):
                        pass
            if "TMP" in values and "REH" in values:
                break
        else:
            logger.warning("기상청 단기예보에서 TMP/REH 데이터 없음 (%s %s)", base_date, base_time)
            return

        at = calc_apparent_temp(values["TMP"], values["REH"], values.get("WSD", 0.0))
        with self._lock:
            self._apparent_temp = round(at, 1)
            self._last_fetched = time.monotonic()
            self._stale = False
        logger.info(
            "체감온도 갱신: %.1f°C (TMP=%.1f REH=%.1f WSD=%.1f grid=%d,%d)",
            at, values["TMP"], values["REH"], values.get("WSD", 0.0), nx, ny,
        )

    def _level(self, t: float) -> HeatLevel:
        if t >= self._severe_temp:
            return "severe"
        if t >= self._warning_temp:
            return "warning"
        if t >= self._caution_temp:
            return "caution"
        return "inactive"

    def get_status(self) -> HeatStatus:
        with self._lock:
            sun_t, shade_t = self._sun_threshold, self._shade_threshold
            c, w, s = self._caution_temp, self._warning_temp, self._severe_temp
            if self._demo_temp is not None:
                return HeatStatus(
                    apparent_temp=self._demo_temp,
                    level=self._level(self._demo_temp),
                    stale=False,
                    demo_mode=True,
                    sun_threshold=sun_t,
                    shade_threshold=shade_t,
                    caution_temp=c,
                    warning_temp=w,
                    severe_temp=s,
                )
            if self._apparent_temp is None:
                return HeatStatus(
                    apparent_temp=None,
                    level="inactive",
                    stale=False,
                    demo_mode=False,
                    sun_threshold=sun_t,
                    shade_threshold=shade_t,
                    caution_temp=c,
                    warning_temp=w,
                    severe_temp=s,
                )
            stale = (time.monotonic() - self._last_fetched) > self.POLL_INTERVAL * 1.5
            return HeatStatus(
                apparent_temp=self._apparent_temp,
                level=self._level(self._apparent_temp),
                stale=stale,
                demo_mode=False,
                sun_threshold=sun_t,
                shade_threshold=shade_t,
                caution_temp=c,
                warning_temp=w,
                severe_temp=s,
            )

    def set_demo_temp(self, temp: float | None):
        with self._lock:
            self._demo_temp = temp

    def set_temp_thresholds(self, caution: float, warning: float, severe: float):
        with self._lock:
            self._caution_temp = caution
            self._warning_temp = warning
            self._severe_temp = severe

    def set_thresholds(self, sun: float, shade: float):
        with self._lock:
            self._sun_threshold = sun
            self._shade_threshold = shade

    def stop(self):
        self._stop.set()


class HeatRegistry:
    """현장(site_id)별 HeatService 인스턴스를 관리하는 싱글턴."""

    def __init__(self):
        self._lock = threading.Lock()
        self._services: dict[int, HeatService] = {}

    def get(
        self,
        site_id: int,
        lat: float | None,
        lon: float | None,
        api_key: str | None,
    ) -> HeatService:
        with self._lock:
            svc = self._services.get(site_id)
            if svc is None or svc._lat != lat or svc._lon != lon or svc._api_key != api_key:
                if svc is not None:
                    svc.stop()
                self._services[site_id] = HeatService(lat, lon, api_key)
            return self._services[site_id]

    def stop_all(self):
        with self._lock:
            for svc in self._services.values():
                svc.stop()
            self._services.clear()

    def stop_site(self, site_id: int):
        with self._lock:
            service = self._services.pop(site_id, None)
        if service is not None:
            service.stop()


heat_registry = HeatRegistry()
