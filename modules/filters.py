STATUS_FILTERS = ("all", "actionable", "completed", "expired", "unsupported")


def quest_status(quest):
    if quest.is_completed():
        return "completed"
    if quest.is_expired():
        return "expired"
    if quest.is_actionable():
        return "actionable"
    return "unsupported"


def normalize_types(types):
    return {value.strip().upper() for value in (types or ()) if value.strip()}


def normalize_statuses(statuses):
    if isinstance(statuses, str):
        return {statuses}
    return {value for value in (statuses or ()) if value}


def matches_quest(quest, status=("actionable", "completed"), types=()):
    task_type = quest.task_type() or "-"
    selected_types = normalize_types(types)
    selected_statuses = normalize_statuses(status)
    return (
        ("all" in selected_statuses or quest_status(quest) in selected_statuses)
        and (not selected_types or task_type in selected_types)
    )


def matches_row(row, status=("actionable", "completed"), types=()):
    _, _, task_type, _, row_status = row
    selected_types = normalize_types(types)
    selected_statuses = normalize_statuses(status)
    return (
        ("all" in selected_statuses or row_status in selected_statuses)
        and (not selected_types or task_type in selected_types)
    )
