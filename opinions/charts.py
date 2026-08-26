"""Server-side SVG chart builders.

We render charts as inline SVG with pre-computed coordinates instead of
sending raw data to a JS chart library. Three reasons:

1. **No JS dep.** The site is otherwise vanilla Django + minimal HTMX;
   adding Chart.js (or similar) for one feature drags ~30KB into every
   judge page. SVG is native, scales cleanly, and the math is one
   function in Python.
2. **Cacheable.** A pre-rendered SVG embedded in the HTML response is
   fully cacheable. JS-rendered charts re-do the layout work every
   page-view.
3. **Accessible / SEO-friendly.** Inline SVG carries text labels in the
   DOM; screen readers and crawlers see the same numbers a sighted
   viewer does.

Each builder returns a plain dict ready to drop into a template that
walks the structure and emits SVG primitives. The template never has
to do math.
"""
from __future__ import annotations

from typing import Optional


# ---- Time-series ("votes per year") chart ------------------------------------

# Default chart dimensions tuned for a desktop card column. The SVG is
# viewBox'd so it scales fluidly on mobile -- the template just sets
# width=100% and the proportions are preserved.
_CHART_WIDTH = 760
_CHART_HEIGHT = 280
_PAD_LEFT = 50
_PAD_RIGHT = 20
_PAD_TOP = 24
_PAD_BOTTOM = 40

# Color families. Judge A is the dossier's primary subject; Judge B is the
# overlay. Keep them distinct from the disposition-pill palette so the
# chart reads as a separate layer of meaning.
_COLOR_A = "#00d9ff"   # cyan
_COLOR_B = "#ff7ed3"   # pink


def _nice_max(value: int) -> int:
    """Round up to a 'nice' axis max so y-axis ticks are integers.

    Avoids axis labels like '23' / '47' -- humans read 25 / 50 faster.
    """
    if value <= 5:
        return max(value, 5)
    if value <= 10:
        return 10
    if value <= 50:
        return ((value // 10) + 1) * 10
    if value <= 200:
        return ((value // 25) + 1) * 25
    if value <= 1000:
        return ((value // 100) + 1) * 100
    return ((value // 500) + 1) * 500


def _tick_step(span: int) -> int:
    """Choose a year-axis tick step based on the visible span."""
    if span <= 5:
        return 1
    if span <= 15:
        return 2
    if span <= 40:
        return 5
    return 10


# ---- Landing hero band ("corpus shape") --------------------------------------

# Wide, short band tuned to sit under the landing headline as a graphic
# rather than a data table. viewBox'd + width:100% in CSS, so it scales
# proportionally on mobile instead of distorting.
_BAND_WIDTH = 1200
_BAND_HEIGHT = 190
_BAND_PAD_TOP = 18       # headroom so the peak never touches the top edge
_BAND_LABEL_BAND = 22    # bottom strip reserved for the sparse year labels


def build_corpus_band(
    rows: list[dict],
    max_ticks: int = 6,
) -> Optional[dict]:
    """Opinions-per-year silhouette for the state landing hero.

    ``rows`` is an ordered list of ``{"year": int, "n": int}``. Years with
    no opinions MUST be present with ``n = 0`` -- the caller fills them
    (see ``_state_year_histogram``). That is deliberate: a coverage hole
    is a true fact about the corpus, and a silhouette that quietly closed
    the gap by interpolating across it would misstate coverage. This is
    the same rule the disposition parser follows -- transcribe, never
    infer.

    Decorative in placement, honest in construction: no y-axis (the band
    is a shape, not a precise read), but the peak year is labeled with
    its real count and the template exposes the whole thing to screen
    readers via <title>/<desc>.

    Returns ``None`` when there is nothing to draw, so the template can
    drop the section entirely rather than render an empty frame.
    """
    rows = [r for r in rows if r.get("year") is not None]
    if len(rows) < 2:
        return None

    year_min = rows[0]["year"]
    year_max = rows[-1]["year"]
    span = year_max - year_min
    if span <= 0:
        return None

    value_max = max(r["n"] for r in rows)
    if value_max <= 0:
        return None

    plot_h = _BAND_HEIGHT - _BAND_PAD_TOP - _BAND_LABEL_BAND
    baseline = _BAND_HEIGHT - _BAND_LABEL_BAND

    def coord(year: int, value: int) -> tuple[float, float]:
        x = (year - year_min) / span * _BAND_WIDTH
        y = baseline - (value / value_max) * plot_h
        return x, y

    pts = [coord(r["year"], r["n"]) for r in rows]

    # Area = the silhouette; line = its lit top edge.
    area = (
        f"M {pts[0][0]:.1f},{baseline:.1f} "
        + " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts)
        + f" L {pts[-1][0]:.1f},{baseline:.1f} Z"
    )
    line = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)

    # Sparse year ticks: first + last always, evenly spaced between, and
    # snapped to decades so the labels read as eras rather than arbitrary
    # years. Deduped in case snapping collides.
    ticks: list[dict] = []
    seen: set[int] = set()
    for i in range(max_ticks):
        raw = year_min + round(span * i / (max_ticks - 1))
        year = year_max if i == max_ticks - 1 else round(raw / 10) * 10
        year = min(max(year, year_min), year_max)
        if year in seen:
            continue
        seen.add(year)
        x, _ = coord(year, 0)
        ticks.append({"year": year, "x": x})

    peak_row = max(rows, key=lambda r: r["n"])
    peak_x, peak_y = coord(peak_row["year"], peak_row["n"])

    return {
        "width": _BAND_WIDTH,
        "height": _BAND_HEIGHT,
        "baseline": baseline,
        "area": area,
        "line": line,
        "ticks": ticks,
        "label_y": _BAND_HEIGHT - 6,
        "year_min": year_min,
        "year_max": year_max,
        "value_max": value_max,
        "peak": {
            "year": peak_row["year"],
            "n": peak_row["n"],
            "x": peak_x,
            "y": peak_y,
            # Nudge the label inboard at the edges so it can't clip.
            "anchor": (
                "start" if peak_x < 90
                else "end" if peak_x > _BAND_WIDTH - 90
                else "middle"
            ),
        },
    }


def build_yearly_votes_chart(
    series_a: list[dict],
    label_a: str,
    series_b: Optional[list[dict]] = None,
    label_b: Optional[str] = None,
) -> Optional[dict]:
    """Convert one or two yearly-vote-count series into SVG-ready payload.

    ``series_a`` / ``series_b`` are ordered lists of
    ``{"year": int, "n": int}`` rows -- one row per year the judge sat,
    counting all panel votes regardless of role. (V1 of the chart; future
    revisions can layer role splits or disposition filtering on top.)

    Returns ``None`` when there's no data to plot. The template guards
    on this so a judge with zero panel votes simply hides the section.
    """
    if not series_a and not series_b:
        return None

    # Union of years across both series defines the x-axis range.
    all_years: set[int] = set()
    for row in series_a:
        all_years.add(row["year"])
    if series_b:
        for row in series_b:
            all_years.add(row["year"])
    if not all_years:
        return None
    year_min = min(all_years)
    year_max = max(all_years)

    # Y-axis max across both series so the lines are comparable.
    all_values: list[int] = [r["n"] for r in series_a]
    if series_b:
        all_values.extend(r["n"] for r in series_b)
    value_max = _nice_max(max(all_values) if all_values else 1)

    chart_w = _CHART_WIDTH - _PAD_LEFT - _PAD_RIGHT
    chart_h = _CHART_HEIGHT - _PAD_TOP - _PAD_BOTTOM

    def coord(year: int, value: int) -> tuple[float, float]:
        if year_max == year_min:
            x = _PAD_LEFT + chart_w / 2
        else:
            x = _PAD_LEFT + (year - year_min) / (year_max - year_min) * chart_w
        y = _PAD_TOP + chart_h - (value / value_max) * chart_h
        return x, y

    def points_str(rows: list[dict]) -> str:
        return " ".join(
            f"{x:.1f},{y:.1f}" for x, y in (coord(r["year"], r["n"]) for r in rows)
        )

    def dots(rows: list[dict]) -> list[dict]:
        # Discrete year markers so single-year series still render as a
        # visible point and not just a degenerate zero-length polyline.
        return [
            {"x": x, "y": y, "year": r["year"], "n": r["n"]}
            for r, (x, y) in ((r, coord(r["year"], r["n"])) for r in rows)
        ]

    series_payload: list[dict] = []
    if series_a:
        series_payload.append({
            "label": label_a,
            "color": _COLOR_A,
            "dash": "",
            "points": points_str(series_a),
            "dots": dots(series_a),
        })
    if series_b:
        series_payload.append({
            "label": label_b or "Comparison",
            "color": _COLOR_B,
            "dash": "6,4",
            "points": points_str(series_b),
            "dots": dots(series_b),
        })

    # Year ticks
    step = _tick_step(year_max - year_min)
    x_ticks: list[dict] = []
    y_cur = year_min
    while y_cur <= year_max:
        x, _ = coord(y_cur, 0)
        x_ticks.append({"year": y_cur, "x": x})
        y_cur += step
    # Always include the last year as a tick even when the step skips it.
    if x_ticks and x_ticks[-1]["year"] != year_max:
        x, _ = coord(year_max, 0)
        x_ticks.append({"year": year_max, "x": x})

    # Value ticks (5 across the y-axis, including zero).
    y_ticks: list[dict] = []
    for i in range(6):
        val = round(value_max * i / 5)
        _, py = coord(year_min, val)
        y_ticks.append({"value": val, "y": py})

    return {
        "width": _CHART_WIDTH,
        "height": _CHART_HEIGHT,
        "pad_left": _PAD_LEFT,
        "pad_top": _PAD_TOP,
        "pad_right": _PAD_RIGHT,
        "pad_bottom": _PAD_BOTTOM,
        "chart_w": chart_w,
        "chart_h": chart_h,
        "axis_y_top": _PAD_TOP,
        "axis_y_bottom": _PAD_TOP + chart_h,
        "axis_x_left": _PAD_LEFT,
        "axis_x_right": _PAD_LEFT + chart_w,
        "year_min": year_min,
        "year_max": year_max,
        "value_max": value_max,
        "x_ticks": x_ticks,
        "y_ticks": y_ticks,
        "series": series_payload,
        "has_overlay": bool(series_b),
    }
