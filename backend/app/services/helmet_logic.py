"""Associate helmet and uncovered-head detections with a person box."""


def box_center(box):
    x1, y1, x2, y2 = box
    return ((x1 + x2) / 2, (y1 + y2) / 2)


def _inside_head_area(person_box, item_box) -> bool:
    x1, y1, x2, y2 = person_box
    head_bottom = y1 + (y2 - y1) / 3
    center_x, center_y = box_center(item_box)
    return x1 <= center_x <= x2 and y1 <= center_y <= head_bottom


def helmet_state_for_person(person_box, helmet_boxes, uncovered_head_boxes) -> bool | None:
    """Return worn, not worn, or unknown from explicit model classes.

    A detected helmet means worn. If no helmet is found, an explicit uncovered
    head confirms non-compliance. When neither class is detected the result is
    unknown, so a missed detection cannot create a false violation event.
    """
    if any(_inside_head_area(person_box, box) for box in helmet_boxes):
        return True
    if any(_inside_head_area(person_box, box) for box in uncovered_head_boxes):
        return False
    return None


def match_helmet_to_person(person_box, helmet_boxes) -> bool:
    """Backward-compatible helper used by lightweight offline scripts."""
    return any(_inside_head_area(person_box, box) for box in helmet_boxes)
