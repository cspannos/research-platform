"""Deterministic vetting checklist for /review and expert context.

Statuses: pass | fail | unclear | unavailable
Computed from stored metrics (no DB persistence) so Phase B/D stubs stay honest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

ChecklistStatus = Literal["pass", "fail", "unclear", "unavailable"]

# Plausible planet transit depth upper bound (~10% of continuum).
_DEPTH_FAIL_PPM = 100_000.0
# Soft caution band (deep but still <10%).
_DEPTH_UNCLEAR_PPM = 50_000.0
# Relative odd/even asymmetry that fails (e.g. 11 vs 116 ppm).
_ODD_EVEN_FAIL_FRAC = 0.40
_ODD_EVEN_UNCLEAR_FRAC = 0.25


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    label: str
    status: ChecklistStatus
    detail: str
    next_action: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _f(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number:  # NaN
            return None
        return number
    return None


def _depth_item(depth_ppm: float | None) -> ChecklistItem:
    if depth_ppm is None:
        return ChecklistItem(
            id="depth",
            label="Depth (baseline-normalized)",
            status="unavailable",
            detail="No depth metric stored.",
            next_action="Re-run /analyze or vet job after light-curve cache exists.",
        )
    if depth_ppm <= 0:
        return ChecklistItem(
            id="depth",
            label="Depth (baseline-normalized)",
            status="fail",
            detail=f"Depth {depth_ppm:.0f} ppm is non-positive — not a transit-like dip.",
            next_action="Inspect phase-fold; confirm flux is continuum-normalized and in-transit.",
        )
    if depth_ppm > _DEPTH_FAIL_PPM:
        pct = depth_ppm / 10_000.0
        return ChecklistItem(
            id="depth",
            label="Depth (baseline-normalized)",
            status="fail",
            detail=(
                f"Depth {depth_ppm:.0f} ppm (~{pct:.1f}% of continuum) exceeds the "
                f"~10% ceiling for a plausible planet — often a normalization or EB/blend issue."
            ),
            next_action=(
                "BLOCKING: verify SAP/PDC flux baseline normalization and pipeline release "
                "before centroid or neighbour work."
            ),
        )
    if depth_ppm > _DEPTH_UNCLEAR_PPM:
        pct = depth_ppm / 10_000.0
        return ChecklistItem(
            id="depth",
            label="Depth (baseline-normalized)",
            status="unclear",
            detail=f"Depth {depth_ppm:.0f} ppm (~{pct:.1f}%) is deep for a planet; check EB/blend.",
            next_action="Compare odd/even and neighbours before treating as planetary.",
        )
    return ChecklistItem(
        id="depth",
        label="Depth (baseline-normalized)",
        status="pass",
        detail=f"Depth {depth_ppm:.0f} ppm is within a plausible planetary range (<10%).",
        next_action="Continue with odd-even and (when available) centroid / neighbours.",
    )


def _odd_even_item(
    odd: float | None,
    even: float | None,
    delta: float | None,
) -> ChecklistItem:
    if odd is None or even is None:
        return ChecklistItem(
            id="odd_even",
            label="Odd–even depths",
            status="unavailable",
            detail="Need both odd and even transit depths (more epochs or re-vet).",
            next_action="Re-run vetting after a longer baseline / more transits.",
        )
    scale = max(abs(odd), abs(even), 1.0)
    asym = abs(odd - even) / scale
    used_delta = delta if delta is not None else abs(odd - even)
    if asym >= _ODD_EVEN_FAIL_FRAC:
        return ChecklistItem(
            id="odd_even",
            label="Odd–even depths",
            status="fail",
            detail=(
                f"Odd {odd:.0f} vs even {even:.0f} ppm (Δ {used_delta:.0f}, "
                f"{asym:.0%} relative) — strong blend / secondary-eclipse red flag."
            ),
            next_action=(
                "Treat as contamination risk: check Gaia neighbours and centroid when available; "
                "do not approve on period/SNR alone."
            ),
        )
    if asym >= _ODD_EVEN_UNCLEAR_FRAC:
        return ChecklistItem(
            id="odd_even",
            label="Odd–even depths",
            status="unclear",
            detail=(
                f"Odd {odd:.0f} vs even {even:.0f} ppm (Δ {used_delta:.0f}, {asym:.0%} relative) "
                f"— mild asymmetry; inspect plots."
            ),
            next_action="Enlarge odd/even plot; confirm enough transits per parity.",
        )
    return ChecklistItem(
        id="odd_even",
        label="Odd–even depths",
        status="pass",
        detail=f"Odd {odd:.0f} / even {even:.0f} ppm are consistent (Δ {used_delta:.0f}).",
        next_action="Proceed to neighbour / centroid checks when Phase B is available.",
    )


def _plots_item(plots_ready: bool, available_plots: list[str] | None) -> ChecklistItem:
    n = len(available_plots or [])
    if plots_ready or n >= 2:
        return ChecklistItem(
            id="plots",
            label="Diagnostic plots",
            status="pass",
            detail=f"{max(n, 2 if plots_ready else 0)} plot(s) available — review phase-fold and odd/even.",
            next_action="Tap plots to enlarge; confirm transit shape looks physical.",
        )
    if n == 1:
        return ChecklistItem(
            id="plots",
            label="Diagnostic plots",
            status="unclear",
            detail="Only one diagnostic plot on disk.",
            next_action="Re-run vet job to regenerate phase-fold / odd-even / periodogram.",
        )
    return ChecklistItem(
        id="plots",
        label="Diagnostic plots",
        status="unavailable",
        detail="No vetting PNGs yet (missing LC cache or vet not run).",
        next_action="Run /ingest then /analyze, or server-side vet_candidate(id).",
    )


def build_vetting_checklist(
    *,
    depth_ppm: float | None,
    odd_depth_ppm: float | None = None,
    even_depth_ppm: float | None = None,
    odd_even_delta_ppm: float | None = None,
    plots_ready: bool = False,
    available_plots: list[str] | None = None,
) -> list[ChecklistItem]:
    items = [
        _depth_item(_f(depth_ppm)),
        _odd_even_item(_f(odd_depth_ppm), _f(even_depth_ppm), _f(odd_even_delta_ppm)),
        _plots_item(bool(plots_ready), available_plots),
        ChecklistItem(
            id="neighbours",
            label="Neighbours / dilution (Gaia)",
            status="unavailable",
            detail="Phase B not deployed yet.",
            next_action="Queue Phase B Gaia cone + dilution estimate.",
        ),
        ChecklistItem(
            id="centroid",
            label="Centroid / aperture",
            status="unavailable",
            detail="Phase B not deployed yet (needs TPF).",
            next_action="Queue Phase B TPF centroid; skip for synthetic LCs.",
        ),
        ChecklistItem(
            id="archive",
            label="Archive / known EB",
            status="unavailable",
            detail="Archive metadata enrichment not deployed yet.",
            next_action="Later: TIC/ExoFOP/SIMBAD flags (Phase D).",
        ),
    ]
    return items


def checklist_blocking_action(items: list[ChecklistItem]) -> str | None:
    """First fail item's next_action, else first unclear, else None."""
    for status in ("fail", "unclear"):
        for item in items:
            if item.status == status:
                return item.next_action
    return None


def format_checklist_for_prompt(items: list[ChecklistItem]) -> str:
    lines = ["Vetting checklist:"]
    for item in items:
        lines.append(f"  - [{item.status}] {item.label}: {item.detail}")
    blocking = checklist_blocking_action(items)
    if blocking:
        lines.append(f"Suggested next action: {blocking}")
    return "\n".join(lines)


def checklist_from_candidate_like(obj: Any, available_plots: list[str] | None = None) -> list[ChecklistItem]:
    plots = available_plots
    if plots is None:
        plots = getattr(obj, "available_plots", None)
    return build_vetting_checklist(
        depth_ppm=_f(getattr(obj, "depth_ppm", None)),
        odd_depth_ppm=_f(getattr(obj, "odd_depth_ppm", None)),
        even_depth_ppm=_f(getattr(obj, "even_depth_ppm", None)),
        odd_even_delta_ppm=_f(getattr(obj, "odd_even_delta_ppm", None)),
        plots_ready=bool(getattr(obj, "plots_ready", False)),
        available_plots=list(plots) if plots else None,
    )
