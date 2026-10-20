"""Episodes from sessions and switch decisions (grouper spec 4.6.2, 4.7, 1.4)."""
from __future__ import annotations

from dataclasses import dataclass, field

from taskmine.group.common import JOINS, related, seconds, subject, untitled, words
from taskmine.group.features import features, furniture_words, profile, weigh
from taskmine.learn import Decision, rule_choice

DIVERSION_MAX = 120.0
RECENT = 90.0
MAX_EPISODE = 7200.0
JOIN_SAVED_GAP = 600.0
GAP_ATTACH = 600.0
NO_JOIN_SAVED = frozenset({"idle", "case_conflict", "new_search"})


@dataclass(eq=False)
class Episode:
    """A run of sessions that is one piece of work."""

    sessions: list = field(default_factory=list)
    close_reason: str | None = None
    diversion: bool = False
    interruption: bool = False
    own_case: bool = False
    gaps: list = field(default_factory=list)

    @property
    def first_seen(self) -> float:
        """Earliest first_seen_at of its sessions."""
        return min(s.first_seen for s in self.sessions)

    @property
    def last_seen(self) -> float:
        """Latest last_seen_at of its sessions."""
        return max(s.last_seen for s in self.sessions)

    @property
    def span(self) -> tuple[float, float]:
        """Earliest and latest `at` of its sessions."""
        return min(s.first_at for s in self.sessions), max(s.last_at for s in self.sessions)


class _Rules:
    """The rule relation over the pass, for the resume check."""

    def __init__(self, sessions):
        self.furniture = furniture_words(sessions)
        self.weights = weigh(sessions)

    def choice(self, context, previous, session) -> str:
        """Rule-table choice for the switch from context into session."""
        return rule_choice(features(context, previous, session, self.furniture, self.weights))[0]


def _resume(state: dict, session, rules: _Rules) -> bool:
    """Step 1 of 4.6.2: a short diversion or interruption returns to the suspended episode."""
    current, suspended = state["current"], state["suspended"]
    if suspended is None or not (current.diversion or current.interruption):
        return False
    span = current.sessions[-1].last_seen - current.sessions[0].first_seen
    if span > DIVERSION_MAX:
        suspended.close_reason = suspended.close_reason or "diversion"
        current.diversion = current.interruption = False
        state["suspended"] = None
        return False
    choice = rules.choice(suspended.sessions, current.sessions[-1], session)
    cases = set().union(*(profile(s).cases for s in suspended.sessions))
    returns = current.diversion or bool(profile(session).cases & cases)
    if choice not in JOINS or not returns:
        return False
    current.close_reason = "diversion" if current.diversion else "break"
    suspended.sessions.append(session)
    suspended.close_reason = None
    state["current"], state["suspended"] = suspended, None
    return True


def _boundary(state: dict, episodes: list, session, decision) -> None:
    """Step 4 of 4.6.2: close the current episode and start a new one."""
    current = state["current"]
    if state["suspended"] is not None:
        state["suspended"].close_reason = state["suspended"].close_reason or "diversion"
        state["suspended"] = None
    current.interruption = False
    gap = session.first_seen - current.sessions[-1].last_seen
    fresh = Episode([session])
    if decision.choice == "diversion":
        current.close_reason, fresh.diversion = "diversion", True
        state["suspended"] = current
    elif decision.reason == "case_conflict" and gap <= RECENT:
        current.close_reason, fresh.interruption = "break", True
        state["suspended"] = current
    else:
        current.close_reason = "idle" if decision.reason == "idle" else "break"
    state["current"] = fresh
    episodes.append(fresh)


def segment(sessions: list, decisions: dict) -> list[Episode]:
    """Episodes in time order; join_saved changes are written back into `decisions`."""
    if not sessions:
        return []
    rules = _Rules(sessions)
    first = Episode([sessions[0]])
    episodes, state = [first], {"current": first, "suspended": None}
    for session in sessions[1:]:
        if _resume(state, session, rules):
            continue
        decision = decisions.get(session.index) or Decision("break", 1.0, "gap", False)
        current = state["current"]
        joins = decision.choice in JOINS
        if joins and session.last_seen - current.sessions[0].first_seen > MAX_EPISODE:
            joins = False
            decision = Decision("break", decision.confidence, decision.reason, decision.hard)
        if joins:
            current.sessions.append(session)
            continue
        _boundary(state, episodes, session, decision)
    if state["suspended"] is not None and state["suspended"] is not state["current"]:
        state["suspended"].close_reason = None
    for episode in episodes:
        episode.sessions.sort(key=lambda s: s.index)
    episodes.sort(key=lambda e: e.sessions[0].index)
    join_saved(episodes, decisions)
    _own_case(episodes, decisions)
    return episodes


def _own_case(episodes: list[Episode], decisions: dict) -> None:
    """Mark episodes that a hard case_conflict starts, and the episodes before them."""
    for i, episode in enumerate(episodes):
        d = decisions.get(episode.sessions[0].index)
        if d is not None and d.hard and d.reason == "case_conflict":
            episode.own_case = True
            if i:
                episodes[i - 1].own_case = True


def _saved_titles(episode: Episode) -> list[str]:
    """Titles of named native sessions when the episode saved or closed a document."""
    if not any(profile(s).saved or profile(s).closed for s in episode.sessions):
        return []
    return [s.title for s in episode.sessions if not s.browser and s.title and not untitled(s.title)]


def _joins_saved(before: Episode, episode: Episode, decisions: dict) -> bool:
    """The 4.7 conditions for a search followed by a late new document saved under its name."""
    d = decisions.get(episode.sessions[0].index)
    if d is None or d.reason in NO_JOIN_SAVED:
        return False
    if not all(profile(s).lookup for s in before.sessions) or _saved_titles(before):
        return False
    first = episode.sessions[0]
    if not profile(first).new_document:
        return False
    if first.first_seen - before.sessions[-1].last_seen > JOIN_SAVED_GAP:
        return False
    searches = [t for s in before.sessions for t in profile(s).search_titles]
    saved = _saved_titles(episode)
    return any(related(subject(a), words(b)) for a in searches for b in saved)


def join_saved(episodes: list[Episode], decisions: dict) -> None:
    """Join a search to a later new document saved under a name that echoes it (4.7)."""
    i = 1
    while i < len(episodes):
        before, episode = episodes[i - 1], episodes[i]
        if not _joins_saved(before, episode, decisions):
            i += 1
            continue
        before.sessions.extend(episode.sessions)
        before.close_reason, before.diversion = episode.close_reason, False
        decisions[episode.sessions[0].index] = Decision("supporting", 0.85, "saved_as_search", True)
        del episodes[i]


def _gap_home(episodes: list[Episode], at: float) -> int:
    """Index of the episode a withheld gap at `at` joins (1.4)."""
    for i, episode in enumerate(episodes):
        start, end = episode.span
        if start <= at <= end:
            return i
    starts = sorted(range(len(episodes)), key=lambda i: episodes[i].span[0])
    for i in starts:
        if 0 <= episodes[i].span[0] - at <= GAP_ATTACH:
            return i
    for i in sorted(range(len(episodes)), key=lambda i: (episodes[i].span[1], i), reverse=True):
        if 0 <= at - episodes[i].span[1] <= GAP_ATTACH:
            return i
    return min(range(len(episodes)), key=lambda i: (abs(episodes[i].span[0] - at), i))


def attach_gaps(episodes: list[Episode], gaps: list[dict]) -> dict[str, int]:
    """Gap id -> index of the episode it joins; the gap is also added to that episode."""
    placed = {}
    if not episodes:
        return placed
    for gap in gaps:
        home = _gap_home(episodes, seconds(gap["at"]))
        episodes[home].gaps.append(gap)
        placed[gap["id"]] = home
    return placed
