"""fzf's matcher, ported: FuzzyMatchV2 scoring plus the extended-search syntax.

Ranking and highlighting come from the same match, so highlighted characters
are exactly the characters that scored. Supported terms, as in fzf:

    sbtrkt    fuzzy            'wild     exact             'wild'   exact word
    ^music    prefix           .mp3$     suffix            ^core$   equal
    !fire     inverse exact    !^music   inverse prefix    a | b    either term

Smart case: a term is case-sensitive only if it contains an uppercase letter. Like fzf, a
term without accents ignores them ("cafe" finds "café"), while one with accents ("é") only
matches them.
"""

import unicodedata
from functools import lru_cache

SCORE_MATCH = 16
SCORE_GAP_START = -3
SCORE_GAP_EXTENSION = -1
BONUS_BOUNDARY = SCORE_MATCH // 2
BONUS_NON_WORD = SCORE_MATCH // 2
BONUS_CAMEL123 = BONUS_BOUNDARY + SCORE_GAP_EXTENSION
BONUS_CONSECUTIVE = -(SCORE_GAP_START + SCORE_GAP_EXTENSION)
BONUS_FIRST_CHAR_MULTIPLIER = 2
BONUS_BOUNDARY_WHITE = BONUS_BOUNDARY + 2
BONUS_BOUNDARY_DELIMITER = BONUS_BOUNDARY + 1

WHITE, NON_WORD, DELIMITER, LOWER, UPPER, LETTER, NUMBER = range(7)
FUZZY, EXACT, BOUNDARY, PREFIX, SUFFIX, EQUAL = range(6)


def char_class(char):
    if char.islower():
        return LOWER
    if char.isupper():
        return UPPER
    if char.isdigit():
        return NUMBER
    if char.isalpha():
        return LETTER
    if char.isspace():
        return WHITE
    return DELIMITER if char in "/,:;|" else NON_WORD


def bonus_for(previous, current):
    if current > NON_WORD:
        if previous == WHITE:
            return BONUS_BOUNDARY_WHITE
        if previous == DELIMITER:
            return BONUS_BOUNDARY_DELIMITER
        if previous == NON_WORD:
            return BONUS_BOUNDARY
    if (previous == LOWER and current == UPPER) or (previous != NUMBER and current == NUMBER):
        return BONUS_CAMEL123
    if current in (NON_WORD, DELIMITER):
        return BONUS_NON_WORD
    return BONUS_BOUNDARY_WHITE if current == WHITE else 0


class Classes(dict):
    """Each character's class as a one-character string, for str.translate; filled as met."""

    def __missing__(self, code):
        self[code] = value = chr(char_class(chr(code)))
        return value


CLASSES = Classes()
BONUS = {(previous, current): bonus_for(previous, current) for previous in range(7) for current in range(7)}


@lru_cache(maxsize=1 << 15)
def bonuses(text):
    """The bonus for a match at each position of `text`, which depends on the text alone.

    Worked out in C, by translating characters to classes and looking up each pair, and
    kept as bytes, so caching one for every item costs a byte per character.
    """
    classes = text.translate(CLASSES).encode("latin-1")
    return bytes(map(BONUS.__getitem__, zip(bytes([WHITE]) + classes, classes)))


# Latin letters without their accents (é → e), as fzf normalizes them. Every item is matched
# on every key press, so this is one str.translate rather than a Python loop per character.
UNACCENTED = {
    code: base
    for code in (*range(0xC0, 0x250), *range(0x1E00, 0x1F00))
    if (base := unicodedata.normalize("NFD", chr(code))[0]).isascii() and base != chr(code)
}


def normalize(text):
    return text.translate(UNACCENTED)


def lower(text):
    # Keep one character per position so match indices stay valid in the original. str.lower
    # does that, except for "İ", which grows, and "Σ", which depends on its neighbors.
    folded = text.lower()
    if len(folded) == len(text) and "Σ" not in text:
        return folded
    return "".join(c if len(c.lower()) != 1 else c.lower() for c in text)


def score_range(text, folded, pattern, start, end):
    """fzf's calculateScore: greedy left-to-right within [start, end)."""
    index, score, in_gap, consecutive, first_bonus = 0, 0, False, 0, 0
    positions = []
    previous = char_class(text[start - 1]) if start else WHITE
    for i in range(start, end):
        current = char_class(text[i])
        if index < len(pattern) and folded[i] == pattern[index]:
            positions.append(i)
            score += SCORE_MATCH
            bonus = bonus_for(previous, current)
            if consecutive == 0:
                first_bonus = bonus
            else:
                if bonus >= BONUS_BOUNDARY and bonus > first_bonus:
                    first_bonus = bonus
                bonus = max(bonus, first_bonus, BONUS_CONSECUTIVE)
            score += bonus * BONUS_FIRST_CHAR_MULTIPLIER if index == 0 else bonus
            in_gap, consecutive, index = False, consecutive + 1, index + 1
        else:
            score += SCORE_GAP_EXTENSION if in_gap else SCORE_GAP_START
            in_gap, consecutive, first_bonus = True, 0, 0
        previous = current
    return score, positions


def fuzzy(text, folded, pattern):
    """fzf's FuzzyMatchV2: the highest-scoring alignment, not just the leftmost one."""
    m = len(pattern)
    if m == 1:
        # The first key press matches every item against one character: visit its occurrences,
        # found in C, keeping the best bonus and stopping at the first word boundary.
        best, best_at, at = -1, 0, folded.find(pattern)
        while at >= 0:
            bonus = bonus_for(char_class(text[at - 1]) if at else WHITE, char_class(text[at]))
            if bonus > best:
                best, best_at = bonus, at
                if bonus >= BONUS_BOUNDARY:
                    break
            at = folded.find(pattern, at + 1)
        return (SCORE_MATCH + best * BONUS_FIRST_CHAR_MULTIPLIER, [best_at]) if best >= 0 else None
    # Most items don't match. Rule them out in C first: every pattern char, in order.
    at = -1
    for char in pattern:
        at = folded.find(char, at + 1)
        if at < 0:
            return None
    # Limit the search to [char before the first pattern char, last occurrence of the last one].
    first = folded.find(pattern[0])
    last = folded.rfind(pattern[-1])
    low = max(0, first - 1)
    chars = folded[low : last + 1]
    n = len(chars)
    # The bonus at `low` itself never counts: the first pattern char comes after it.
    bonus_at = bonuses(text)[low : last + 1]

    # Phase 2: first-row scores and the earliest match of each pattern char. The loops below
    # run per character of every candidate, so they avoid calls and repeated indexing.
    h0, c0, firsts = [0] * n, [0] * n, []
    best, best_at, value, in_gap, last_match = 0, 0, 0, False, 0
    head, wanted, found = pattern[0], pattern[0], 0
    for at in range(n):
        char = chars[at]
        if char == wanted:
            if found < m:
                firsts.append(at)
                found += 1
                wanted = pattern[min(found, m - 1)]
            last_match = at
        if char == head:
            value = h0[at] = SCORE_MATCH + bonus_at[at] * BONUS_FIRST_CHAR_MULTIPLIER
            c0[at] = 1
            in_gap = False
        else:
            value += SCORE_GAP_EXTENSION if in_gap else SCORE_GAP_START
            if value < 0:
                value = 0
            h0[at] = value
            in_gap = True
    if found != m:
        return None

    # Phase 3: fill the score matrix row by row (one row per pattern character).
    f0 = firsts[0]
    width = last_match - f0 + 1
    h = h0[f0 : last_match + 1] + [0] * (width * (m - 1))
    c = c0[f0 : last_match + 1] + [0] * (width * (m - 1))
    for index in range(1, m):
        # h[here + column] is this row's cell, h[diagonal + column] the one up and to the left.
        # Rows past the first start zeroed, so cells that don't match need no c write.
        char, here, in_gap, left, last_row = pattern[index], index * width - f0, False, 0, index == m - 1
        diagonal = here - width - 1
        for column in range(firsts[index], last_match + 1):
            s2 = left + (SCORE_GAP_EXTENSION if in_gap else SCORE_GAP_START)
            if chars[column] == char:
                s1 = h[diagonal + column] + SCORE_MATCH
                bonus = bonus_at[column]
                consecutive = c[diagonal + column] + 1
                if consecutive > 1:
                    first_bonus = bonus_at[column - consecutive + 1]
                    if bonus >= BONUS_BOUNDARY and bonus > first_bonus:
                        consecutive = 1  # Break the consecutive chunk at a stronger boundary.
                    else:
                        bonus = max(bonus, BONUS_CONSECUTIVE, first_bonus)
                if s1 + bonus < s2:
                    s1 += bonus_at[column]
                    consecutive = 0
                else:
                    s1 += bonus
                c[here + column] = consecutive
                in_gap = s1 < s2
                left = s1 if s1 > s2 else s2
            else:
                in_gap = s2 > 0
                left = s2
            if left < 0:
                left = 0
            h[here + column] = left
            if last_row and left > best:
                best, best_at = left, column

    # Phase 4: backtrace the positions of the best alignment.
    positions, index, column, prefer_match = [], m - 1, best_at, True
    while True:
        row, j = index * width, column - f0
        score = h[row + j]
        diagonal = h[row - width + j - 1] if index > 0 and column >= firsts[index] else 0
        left = h[row + j - 1] if column > firsts[index] else 0
        if score > diagonal and (score > left or (score == left and prefer_match)):
            positions.append(low + column)
            if index == 0:
                break
            index -= 1
        prefer_match = c[row + j] > 1 or (row + width + j + 1 < len(c) and c[row + width + j + 1] > 0)
        column -= 1
    positions.reverse()
    return best, positions


def exact(text, folded, pattern, boundary=False):
    best, best_bonus = None, -1
    at = folded.find(pattern)
    while at >= 0:
        end = at + len(pattern)
        if not boundary or (
            (at == 0 or char_class(text[at - 1]) <= DELIMITER)
            and (end == len(text) or char_class(text[end]) <= DELIMITER)
        ):
            bonus = bonus_for(char_class(text[at - 1]) if at else WHITE, char_class(text[at]))
            if bonus > best_bonus:
                best, best_bonus = at, bonus
            if bonus >= BONUS_BOUNDARY:
                break
        at = folded.find(pattern, at + 1)
    return None if best is None else score_range(text, folded, pattern, best, best + len(pattern))


def anchored(text, folded, pattern, kind):
    start = 0 if pattern[:1].isspace() else len(folded) - len(folded.lstrip())
    end = len(folded) if pattern[-1:].isspace() else len(folded.rstrip())
    if kind == PREFIX:
        end = start + len(pattern)
    elif kind == SUFFIX:
        start = end - len(pattern)
    if start < 0 or end > len(folded) or end - start != len(pattern) or folded[start:end] != pattern:
        return None
    return score_range(text, folded, pattern, start, end)


def match_term(term, text, haystacks):
    """Match one term; `haystacks` are the text as-is, lowercased, unaccented, and both."""
    kind, pattern, case_sensitive, accented = term[0], term[2], term[3], term[4]
    haystack = haystacks[(0 if case_sensitive else 1) + (0 if accented else 2)]
    if kind == FUZZY:
        return fuzzy(text, haystack, pattern)
    if kind in (EXACT, BOUNDARY):
        return exact(text, haystack, pattern, kind == BOUNDARY)
    return anchored(text, haystack, pattern, kind)


def parse(query):
    """fzf's parseTerms: AND of OR-sets of (kind, inverse, text, case_sensitive, accented)."""
    sets, current, after_bar = [], [], False
    for token in query.replace("\\ ", "\t").split(" "):
        text = token.replace("\t", " ")
        if not text:
            continue
        if current and not after_bar and text == "|":
            after_bar = True
            continue
        case_sensitive = text != text.lower()
        kind, inverse = FUZZY, False
        if text.startswith("!"):
            kind, inverse, text = EXACT, True, text[1:]
        if text != "$" and text.endswith("$"):
            kind, text = SUFFIX, text[:-1]
        if len(text) > 2 and text.startswith("'") and text.endswith("'"):
            kind, text = BOUNDARY, text[1:-1]
        elif text.startswith("'"):
            kind, text = (FUZZY if inverse else EXACT), text[1:]
        elif text.startswith("^"):
            kind, text = (EQUAL if kind == SUFFIX else PREFIX), text[1:]
        if text:
            term = (kind, inverse, text if case_sensitive else lower(text), case_sensitive, normalize(text) != text)
            if after_bar:
                current.append(term)
            else:
                if current:
                    sets.append(current)
                current = [term]
        after_bar = False
    if current:
        sets.append(current)
    return sets


def remember(seen, text, compute):
    if text not in seen:
        if len(seen) > 1 << 16:
            seen.clear()  # Titles change for as long as the picker stays open.
        seen[text] = compute()
    return seen[text]


class Query:
    def __init__(self, text, previous=None):
        """`previous`, the query before this one, lends what its terms matched."""
        self.text = text
        self.sets = parse(text)
        # What each text matched, for the whole query and for each term. After a refresh only
        # changed panes are matched again, and typing on reuses shared terms: "fix r" keeps "fix".
        self.seen = {}
        kept = previous.terms if previous else {}
        self.terms = {term: kept.get(term, {}) for terms in self.sets for term in terms}

    def __bool__(self):
        return bool(self.sets)

    def match(self, text):
        """Return (score, sorted positions) or None, with fzf's extendedMatch semantics."""
        return remember(self.seen, text, lambda: self.score(text))

    def score(self, text):
        haystacks = []

        def run(term):
            if not haystacks:
                normalized = normalize(text)
                haystacks.extend((text, lower(text), normalized, lower(normalized)))
            return match_term(term, text, haystacks)

        total, positions = 0, set()
        for terms in self.sets:
            matched = False
            for term in terms:
                result = remember(self.terms[term], text, lambda term=term: run(term))
                if result and not term[1]:
                    total += result[0]
                    positions.update(result[1])
                    matched = True
                    break
                if not result and term[1]:
                    matched = True
            if not matched:
                return None
        return total, sorted(positions)


def rank(items, query, text=lambda item: item):
    """Matching items as (item, positions), best score first; ties keep input order."""
    query = query if isinstance(query, Query) else Query(query)
    if not query:
        return [(item, []) for item in items]
    scored = []
    for order, item in enumerate(items):
        result = query.match(text(item))
        if result:
            scored.append((-result[0], order, item, result[1]))
    scored.sort(key=lambda row: row[:2])
    return [(item, positions) for _, _, item, positions in scored]
