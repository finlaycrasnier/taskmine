"""Switch features (grouper spec 4.2 to 4.4): 12 flags and 8 numbers per session switch."""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from taskmine.group.common import EXTENSION, seconds, subject, title_of, untitled, words
from taskmine.group.sessions import Session
from taskmine.group.steps import step_kind

FLAGS = ("idle", "case_conflict", "shared_anchor", "same_surface", "copy_paste",
         "search_then_create", "new_search", "context_saved", "shared_words", "recent",
         "same_app", "closed")
NUMBERS = ("gap", "prev_seconds", "context_seconds", "link_strong", "link_weak",
           "hour_sin", "hour_cos", "to_browser")
IDLE, RECENT, SEARCH_TO_DOCUMENT, SAME_SURFACE = 600.0, 90.0, 90.0, 120.0
STRONG_LINK, WEAK_LINK = 0.5, 1.0
TEXT_BUDGET, TITLE_LIMIT, APP_LIMIT = 6000, 500, 200
LOW_SIGNAL = frozenset({"view", "click"})
DOCUMENT_WORK = frozenset({"create", "type", "paste", "save"})


@dataclass
class Profile:
    """Per-session features of 4.2, kept in memory only."""

    title_words: set = field(default_factory=set)
    kind_words: set = field(default_factory=set)
    text_words: set = field(default_factory=set)
    app_words: set = field(default_factory=set)
    kinds: list = field(default_factory=list)
    closed: bool = False
    copy_time: float | None = None
    first_paste: float | None = None
    browser: bool = False
    search_titles: set = field(default_factory=set)
    search: bool = False
    lookup: bool = False
    new_document: bool = False
    document_work: bool = False
    saved: bool = False
    windows: set = field(default_factory=set)
    artifacts: set = field(default_factory=set)
    cases: set = field(default_factory=set)
    word_weights: dict = field(default_factory=dict)
    strong_words: set = field(default_factory=set)

    @property
    def all_words(self) -> set:
        """Title, text and kind words."""
        return self.title_words | self.text_words | self.kind_words


def _title_words(items: list[dict]) -> tuple[set, set]:
    """Title words without file extensions, and the extension words."""
    titles, kinds = set(), set()
    for item in items:
        title = title_of(item)[:TITLE_LIMIT]
        ext = EXTENSION.search(title)
        if ext:
            kinds |= set(words(ext.group()))
            title = title[:ext.start()]
        titles |= set(words(title))
    return titles, kinds - titles


def _text_words(items: list[dict]) -> set:
    """Words of the item texts in order, within the session budget."""
    budget, out = TEXT_BUDGET, set()
    for item in items:
        text = item.get("text") or ""
        if not text or budget <= 0:
            continue
        out |= set(words(text[:budget]))
        budget -= len(text)
    return out


def _kinds(items: list[dict], profile: Profile) -> None:
    """Step kinds, copy and paste times, searches."""
    for item in items:
        kind = step_kind(item)
        if kind is None:
            continue
        profile.kinds.append(kind)
        at = seconds(item["at"])
        if kind == "copy":
            profile.copy_time = at if profile.copy_time is None else max(profile.copy_time, at)
        if kind == "paste":
            profile.first_paste = at if profile.first_paste is None else min(profile.first_paste, at)
        if kind == "search" and title_of(item):
            profile.search_titles.add(title_of(item).casefold())
    meaningful = [k for k in profile.kinds if k not in LOW_SIGNAL]
    profile.closed = bool(meaningful) and meaningful[-1] == "close"


def _anchors(items: list[dict], profile: Profile) -> None:
    """Union of window, artifact and case anchors, and the noise word weights."""
    for item in items:
        if item.get("window_key"):
            profile.windows.add(item["window_key"])
        if item.get("artifact_key"):
            profile.artifacts.add(item["artifact_key"])
        profile.cases |= {tuple(p) for p in item.get("case_keys") or ()}
        for word, weight in (item.get("noise_words") or {}).items():
            profile.word_weights[word] = max(weight, profile.word_weights.get(word, 0.0))
        profile.strong_words |= set(item.get("used_words") or ())


def profile(session: Session) -> Profile:
    """The 4.2 features of a session, computed once."""
    found = getattr(session, "_profile", None)
    if found is not None:
        return found
    items = session.items
    p = Profile()
    p.title_words, p.kind_words = _title_words(items)
    p.text_words = _text_words(items)
    p.app_words = set(words(f"{session.app_label} {session.app}", APP_LIMIT))
    _kinds(items, p)
    _anchors(items, p)
    p.browser = session.browser
    p.search = bool(p.search_titles) or "search" in p.kinds
    p.lookup = p.browser and bool({"search", "browse"} & set(p.kinds))
    p.new_document = (not p.browser and untitled(session.title)
                      and bool({"create", "type"} & set(p.kinds)))
    p.document_work = bool(DOCUMENT_WORK & set(p.kinds))
    p.saved = "save" in p.kinds
    session._profile = p
    return p


def furniture_words(sessions: list[Session]) -> set[str]:
    """Title words two windows of one app show with other-app work between them (4.3)."""
    shown = {s.index: profile(s).title_words | profile(s).text_words for s in sessions}
    out: set[str] = set()
    by_app: dict[str, list[Session]] = {}
    for s in sessions:
        by_app.setdefault(s.app, []).append(s)
    for group in by_app.values():
        for word in set().union(*(profile(s).title_words for s in group)):
            holders = [s for s in group if word in profile(s).title_words]
            for left, right in itertools.pairwise(holders):
                between = [s for s in sessions[left.index + 1:right.index] if s.app != left.app]
                if (left.identity != right.identity and between
                        and not any(word in shown[s.index] for s in between)):
                    out.add(word)
                    break
    return out


def weigh(sessions: list[Session]) -> dict[int, dict[str, float]]:
    """Link weight of every word of every session: rarity times noise weight (4.3)."""
    total = len(sessions)
    counts: dict[str, int] = {}
    for s in sessions:
        for word in profile(s).all_words:
            counts[word] = counts.get(word, 0) + 1
    out = {}
    for s in sessions:
        p, weights = profile(s), {}
        for word in p.all_words:
            rarity = min(1.0, math.log(1 + total / counts[word]) / math.log(3)) if total >= 3 else 1.0
            weights[word] = 1.0 if word in p.strong_words else p.word_weights.get(word, 1.0) * rarity
        out[s.index] = weights
    return out


def _link(shared: set, context_weights: dict, session_weights: dict) -> float:
    """Summed weight of shared words, each the lower of the two sides."""
    return sum(min(context_weights.get(w, 1.0), session_weights.get(w, 1.0)) for w in shared)


def _new_search(context: list[Session], session: Session, p: Profile, ignored: set,
                title_words: set) -> bool:
    """A fresh search on a new subject after document work (signal 7)."""
    if not p.search or p.document_work or not any(profile(s).document_work for s in context):
        return False
    if session.app in {s.app for s in context if not s.browser}:
        return False
    topic = set()
    for title in p.search_titles or {session.title.casefold()}:
        topic |= set(subject(title))
    return not (topic - ignored) & title_words


def _context_weights(context: list[Session], weights: dict) -> dict[str, float]:
    """Highest link weight of each word over the context sessions."""
    out: dict[str, float] = {}
    for s in context:
        for word, w in weights.get(s.index, {}).items():
            out[word] = max(w, out.get(word, 0.0))
    return out


def _flags(context, previous, session, furniture, weights) -> tuple[dict, float, float]:
    """The twelve signals of 4.4 and the two link values."""
    p, prev = profile(session), profile(previous)
    ctx = [profile(s) for s in context]
    gap = session.first_seen - previous.last_seen
    title_words = set().union(*(c.title_words for c in ctx))
    all_words = set().union(*(c.all_words for c in ctx))
    cases = set().union(*(c.cases for c in ctx))
    ignored = furniture | p.app_words | set().union(*(c.app_words for c in ctx))
    context_w, session_w = _context_weights(context, weights), weights.get(session.index, {})
    strong = (title_words & (p.title_words | p.text_words)) - ignored
    weak = (all_words & (p.title_words | p.kind_words)) - ignored
    strong_link, weak_link = _link(strong, context_w, session_w), _link(weak, context_w, session_w)
    right_extra = any(v not in {c[1] for c in cases if c[0] == n} for n, v in p.cases
                      if n in {c[0] for c in cases})
    flags = {
        "idle": gap > IDLE,
        "case_conflict": bool(cases and p.cases) and (not cases & p.cases or right_extra),
        "shared_anchor": bool(cases & p.cases or set().union(*(c.artifacts for c in ctx)) & p.artifacts
                              or prev.windows & p.windows),
        "same_surface": (session.identity in {s.identity for s in context} and gap <= SAME_SURFACE
                         and "|title:" not in session.key),
        "copy_paste": (prev.copy_time is not None and p.first_paste is not None
                       and 0 <= p.first_paste - prev.copy_time <= RECENT),
        "search_then_create": p.new_document and prev.lookup and gap <= SEARCH_TO_DOCUMENT,
        "new_search": _new_search(context, session, p, ignored, title_words),
        "context_saved": any(c.saved or c.closed for c in ctx),
        "shared_words": strong_link >= STRONG_LINK or (len(weak) >= 2 and weak_link >= WEAK_LINK),
        "recent": gap <= RECENT,
        "same_app": session.app in {s.app for s in context},
        "closed": prev.closed,
    }
    return flags, strong_link, weak_link


def features(context: list[Session], previous: Session, session: Session,
             furniture: set | None = None, weights: dict | None = None) -> dict[str, float]:
    """The 12 flags (0 or 1) and the 8 numbers of the switch from context into session."""
    furniture = furniture if furniture is not None else set()
    weights = weights if weights is not None else {}
    flags, strong, weak = _flags(context, previous, session, furniture, weights)
    gap = session.first_seen - previous.last_seen
    span = max(s.last_seen for s in context) - min(s.first_seen for s in context)
    hour = (session.first_seen % 86400) / 3600.0
    out = {name: int(bool(flags[name])) for name in FLAGS}
    out.update(gap=math.log1p(max(0.0, gap)), prev_seconds=math.log1p(previous.duration),
               context_seconds=math.log1p(max(0.0, span)), link_strong=strong, link_weak=weak,
               hour_sin=math.sin(2 * math.pi * hour / 24), hour_cos=math.cos(2 * math.pi * hour / 24),
               to_browser=float(session.browser))
    return out
