"""안전모 착용 판정 + N프레임 연속 오탐 억제."""

VIOLATION_FRAMES = 15   # 이 횟수 연속 미착용일 때만 이벤트 (깜빡임 방지)


def box_center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def match_helmet_to_person(person_box, helmet_boxes) -> bool:
    """helmet 박스 중심이 person 박스 상단 1/3 안에 있으면 착용으로 판정."""
    x1, y1, x2, y2 = person_box
    top_y = y1 + (y2 - y1) / 3
    for hb in helmet_boxes:
        cx, cy = box_center(hb)
        if x1 <= cx <= x2 and y1 <= cy <= top_y:
            return True
    return False


def find_violations(detections) -> list[dict]:
    """감지 결과에서 미착용자 목록 추출."""
    no_helmets = [d for d in detections if d["cls"] == "no-helmet"]
    if no_helmets:                      # 우리 모델은 head를 직접 잡으므로 여기서 끝
        return no_helmets

    # 예비 로직: person+helmet 매칭 방식 (모델 교체 대비)
    persons = [d for d in detections if d["cls"] == "person"]
    helmets = [d["box"] for d in detections if d["cls"] == "helmet"]
    return [p for p in persons
            if not match_helmet_to_person(p["box"], helmets)]


class ViolationTracker:
    """미착용이 N프레임 연속일 때 딱 1회만 True."""
    def __init__(self, threshold: int = VIOLATION_FRAMES):
        self.threshold = threshold
        self.streak = 0

    def update(self, violation_count: int) -> bool:
        if violation_count > 0:
            self.streak += 1
        else:
            self.streak = 0
        return self.streak == self.threshold