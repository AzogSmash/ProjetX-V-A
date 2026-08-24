AI_ACTIVITY_WINDOW_DAYS = 30
AI_MIN_ACTIVE_PLAYERS_PER_JOB = 2
AI_MAX_PER_JOB = 1
AI_EFFICIENCY_PERCENT = 60


def ai_is_needed(active_players: int) -> bool:
    return active_players < AI_MIN_ACTIVE_PLAYERS_PER_JOB
