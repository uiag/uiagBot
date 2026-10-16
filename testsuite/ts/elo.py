"""Rating mathematics.

Everything uses the logistic (FIDE/Elo) model

    E(d) = 1 / (1 + 10**(-d/400))

with a draw counted as half a win.  That is the same model cutechess-cli and
fishtest report, so the numbers here are comparable to the ones you see quoted
for other engines.

The three things this module provides:

* :func:`match_result` -- score, Elo difference and a 95% interval for one
  head-to-head match, plus the likelihood of superiority.
* :func:`sprt` -- the sequential test used to decide whether a change to the
  engine is an improvement, without playing a fixed number of games.
* :func:`fit_rating` -- a maximum-likelihood rating for the engine from games
  against several opponents whose ratings are known (the Elo ladder).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

LN10_OVER_400 = math.log(10.0) / 400.0
#: Scores closer to 0 or 1 than this are treated as that bound; a 100% score
#: has no finite Elo, so results get reported as "> x" instead.
EPS = 1e-9


def expected_score(elo_diff: float) -> float:
    """Expected score of a player rated `elo_diff` above the opponent."""
    return 1.0 / (1.0 + math.pow(10.0, -elo_diff / 400.0))


def elo_diff(score: float) -> float:
    """Elo difference implied by a score fraction; +-inf for 1.0 and 0.0."""
    if score >= 1.0 - EPS:
        return math.inf
    if score <= EPS:
        return -math.inf
    return -400.0 * math.log10(1.0 / score - 1.0)


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def likelihood_of_superiority(wins: int, losses: int) -> float:
    """P(the engine is really better), from the decisive games alone."""
    if wins + losses == 0:
        return 0.5
    return _phi((wins - losses) / math.sqrt(2.0 * (wins + losses)))


# -------------------------------------------------------------- single match


@dataclass
class MatchStats:
    wins: int
    draws: int
    losses: int
    score: float = 0.0
    games: int = 0
    draw_rate: float = 0.0
    #: standard error of the score fraction
    stderr: float = 0.0
    elo: float = 0.0
    elo_low: float = 0.0
    elo_high: float = 0.0
    los: float = 0.5

    @property
    def points(self) -> float:
        return self.wins + 0.5 * self.draws

    def score_line(self) -> str:
        return (f"{self.points:g}/{self.games} "
                f"(+{self.wins} ={self.draws} -{self.losses})")

    def elo_line(self) -> str:
        if math.isinf(self.elo):
            return "+inf" if self.elo > 0 else "-inf"
        return f"{self.elo:+.0f} [{self.elo_low:+.0f}, {self.elo_high:+.0f}]"


def match_result(wins: int, draws: int, losses: int,
                 confidence: float = 0.95) -> MatchStats:
    """Score and Elo difference with a confidence interval."""
    out = MatchStats(wins=wins, draws=draws, losses=losses)
    n = wins + draws + losses
    out.games = n
    if n == 0:
        return out

    score = (wins + 0.5 * draws) / n
    out.score = score
    out.draw_rate = draws / n
    # Sample variance of the per-game score, then the error of its mean.
    var = (wins * (1.0 - score) ** 2 + draws * (0.5 - score) ** 2
           + losses * score ** 2) / n
    out.stderr = math.sqrt(var / n)

    z = _z_for(confidence)
    out.elo = elo_diff(score)
    out.elo_low = elo_diff(min(1.0, max(0.0, score - z * out.stderr)))
    out.elo_high = elo_diff(min(1.0, max(0.0, score + z * out.stderr)))
    out.los = likelihood_of_superiority(wins, losses)
    return out


def _z_for(confidence: float) -> float:
    """Two-sided normal quantile; exact enough for the usual confidences."""
    table = {0.68: 0.9945, 0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}
    if confidence in table:
        return table[confidence]
    # Newton on the CDF; the interval is tiny so this always converges.
    target = 0.5 + confidence / 2.0
    x = 2.0
    for _ in range(60):
        err = _phi(x) - target
        pdf = math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
        if pdf < 1e-12:
            break
        x -= err / pdf
    return abs(x)


# ---------------------------------------------------------------------- SPRT


@dataclass
class SprtResult:
    llr: float
    lower: float
    upper: float
    verdict: str          # "H0" (no gain), "H1" (gain), "continue"
    elo0: float
    elo1: float
    games: int

    def line(self) -> str:
        return (f"LLR {self.llr:+.2f} "
                f"[{self.lower:.2f}, {self.upper:.2f}] "
                f"H0 {self.elo0:+g} / H1 {self.elo1:+g}  ->  {self.verdict}")


def sprt(wins: int, draws: int, losses: int, elo0: float = 0.0, elo1: float = 10.0,
         alpha: float = 0.05, beta: float = 0.05) -> SprtResult:
    """Sequential probability ratio test on the trinomial result.

    H0: the Elo difference is `elo0`.  H1: it is `elo1`.  The draw rate is a
    nuisance parameter taken from the observed games, which is the usual
    generalised-likelihood form used by fishtest.
    """
    n = wins + draws + losses
    lower = math.log(beta / (1.0 - alpha))
    upper = math.log((1.0 - beta) / alpha)
    if n == 0:
        return SprtResult(0.0, lower, upper, "continue", elo0, elo1, 0)

    draw_rate = draws / n
    llr = 0.0
    probs = []
    for elo in (elo0, elo1):
        score = expected_score(elo)
        pd = min(draw_rate, 2.0 * min(score, 1.0 - score) - 1e-6)
        pd = max(pd, 0.0)
        pw = score - pd / 2.0
        pl = 1.0 - score - pd / 2.0
        probs.append((max(pw, 1e-9), max(pd, 1e-9), max(pl, 1e-9)))

    (pw0, pd0, pl0), (pw1, pd1, pl1) = probs
    llr += wins * math.log(pw1 / pw0)
    llr += draws * math.log(pd1 / pd0)
    llr += losses * math.log(pl1 / pl0)

    verdict = "continue"
    if llr >= upper:
        verdict = "H1"
    elif llr <= lower:
        verdict = "H0"
    return SprtResult(llr, lower, upper, verdict, elo0, elo1, n)


# ------------------------------------------------------- rating from a ladder


@dataclass
class OpponentResult:
    """Games against one opponent of known rating."""

    name: str
    elo: float
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def games(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def points(self) -> float:
        return self.wins + 0.5 * self.draws

    def merged(self, other: "OpponentResult") -> "OpponentResult":
        return OpponentResult(self.name, self.elo, self.wins + other.wins,
                              self.draws + other.draws, self.losses + other.losses)


@dataclass
class RatingEstimate:
    elo: float
    stderr: float
    low: float
    high: float
    games: int
    points: float
    draw_rate: float
    bounded: str = ""          # "below"/"above" when the score was 0% or 100%
    opponents: list[OpponentResult] = field(default_factory=list)
    confidence: float = 0.95

    def line(self) -> str:
        if self.bounded == "above":
            return f">{self.elo:.0f} (won every game of {self.games})"
        if self.bounded == "below":
            return f"<{self.elo:.0f} (lost every game of {self.games})"
        return (f"{self.elo:.0f} +- {self.stderr:.0f} "
                f"[{self.low:.0f}, {self.high:.0f}] "
                f"over {self.games} games")


def fit_rating(results: Sequence[OpponentResult], confidence: float = 0.95,
               search_margin: float = 1500.0) -> RatingEstimate:
    """Maximum-likelihood rating against opponents with known ratings.

    Solves ``sum_i n_i * E(R - R_i) = sum_i points_i`` for R, which is the
    exact maximum-likelihood estimate of the Bradley-Terry model when a draw
    counts as half a win.  The standard error comes from the delta method with
    a model variance of ``E(1-E) - p_draw/4`` per game.
    """
    played = [r for r in results if r.games > 0]
    total_games = sum(r.games for r in played)
    total_points = sum(r.points for r in played)
    total_draws = sum(r.draws for r in played)

    out = RatingEstimate(elo=0.0, stderr=0.0, low=0.0, high=0.0,
                         games=total_games, points=total_points,
                         draw_rate=(total_draws / total_games) if total_games else 0.0,
                         opponents=list(played), confidence=confidence)
    if not played:
        return out

    lo = min(r.elo for r in played) - search_margin
    hi = max(r.elo for r in played) + search_margin

    def surplus(rating: float) -> float:
        return sum(r.games * expected_score(rating - r.elo) for r in played) - total_points

    if total_points <= EPS:
        out.elo, out.bounded = lo, "below"
    elif total_points >= total_games - EPS:
        out.elo, out.bounded = hi, "above"
    else:
        for _ in range(200):                       # bisection: surplus is monotone
            mid = 0.5 * (lo + hi)
            if surplus(mid) < 0.0:
                lo = mid
            else:
                hi = mid
        out.elo = 0.5 * (lo + hi)

    # delta method: SE(R) = sd(total score) / d(expected total score)/dR
    pd = out.draw_rate
    variance = 0.0
    slope = 0.0
    for r in played:
        e = expected_score(out.elo - r.elo)
        variance += r.games * max(e * (1.0 - e) - pd / 4.0, 1e-4)
        slope += r.games * e * (1.0 - e) * LN10_OVER_400
    out.stderr = math.sqrt(variance) / slope if slope > 1e-12 else float("inf")

    z = _z_for(confidence)
    out.low = out.elo - z * out.stderr
    out.high = out.elo + z * out.stderr
    return out


def merge_results(results: Iterable[OpponentResult]) -> list[OpponentResult]:
    """Add up several result records per opponent, keyed by name."""
    merged: dict[str, OpponentResult] = {}
    for r in results:
        if r.name in merged:
            merged[r.name] = merged[r.name].merged(r)
        else:
            merged[r.name] = OpponentResult(r.name, r.elo, r.wins, r.draws, r.losses)
    return sorted(merged.values(), key=lambda r: r.elo)


def information_per_game(rating: float, opponent_elo: float) -> float:
    """How much one game against this opponent tells us about `rating`.

    Fisher information of the logistic model: largest for an equal opponent,
    and it falls off fast, which is why the ladder plays the nearest rungs.
    """
    e = expected_score(rating - opponent_elo)
    return e * (1.0 - e)


def pick_opponents(rating: float, opponents: Sequence[OpponentResult],
                   count: int = 3) -> list[OpponentResult]:
    """Choose the most informative rungs around the current estimate.

    Takes the nearest `count` opponents but keeps at least one on each side of
    the estimate when possible, so the fit is not extrapolating off one end.
    """
    if not opponents:
        return []
    count = max(1, min(count, len(opponents)))
    by_distance = sorted(opponents, key=lambda o: abs(o.elo - rating))
    chosen = by_distance[:count]
    if count >= 2:
        below = [o for o in by_distance if o.elo <= rating]
        above = [o for o in by_distance if o.elo > rating]
        if below and not any(o.elo <= rating for o in chosen):
            chosen[-1] = below[0]
        elif above and not any(o.elo > rating for o in chosen):
            chosen[-1] = above[0]
    unique = {o.name: o for o in chosen}
    return sorted(unique.values(), key=lambda o: o.elo)
