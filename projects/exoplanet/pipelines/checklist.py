"""Deterministic vetting checklist for /review and expert context.

Statuses: pass | fail | unclear | unavailable
Computed from stored metrics (no DB persistence) so Phase B/D stubs stay honest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from projects.exoplanet.pipelines.neighbours import (
    BRIGHT_FAIL_DMAG,
    BRIGHT_UNCLEAR_DMAG,
    DILUTION_FAIL,
    DILUTION_UNCLEAR,
    loads_payload,
)

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
        next_action="Proceed to neighbour / centroid checks.",
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


def _neighbours_item(neighbours: dict[str, Any] | None) -> ChecklistItem:
    payload = neighbours if isinstance(neighbours, dict) else None
    if not payload or payload.get("status") == "unavailable":
        reason = (payload or {}).get("reason") or "Gaia cone not run yet."
        return ChecklistItem(
            id="neighbours",
            label="Neighbours / dilution (Gaia)",
            status="unavailable",
            detail=f"Neighbour check unavailable ({reason}).",
            next_action="Re-run vet_neighbours after network/catalog access, or set ra/dec on the target.",
        )
    n = payload.get("n_neighbours")
    n_txt = "unknown" if n is None else str(int(n))
    dmag = payload.get("brightest_delta_mag")
    dil = payload.get("dilution")
    bits = [f"{n_txt} Gaia neighbours within 1'"]
    if isinstance(dmag, (int, float)):
        bits.append(f"brightest ΔG={dmag:.2f}")
    if isinstance(dil, (int, float)):
        bits.append(f"dilution={dil:.2f}")
    detail = "; ".join(bits)
    if isinstance(dil, (int, float)) and dil < DILUTION_FAIL:
        return ChecklistItem(
            id="neighbours",
            label="Neighbours / dilution (Gaia)",
            status="fail",
            detail=detail + " — heavy contamination likely.",
            next_action="Do not approve on SNR alone; inspect neighbour table and centroid.",
        )
    if isinstance(dmag, (int, float)) and dmag < BRIGHT_FAIL_DMAG:
        return ChecklistItem(
            id="neighbours",
            label="Neighbours / dilution (Gaia)",
            status="fail",
            detail=detail + " — comparably bright neighbour.",
            next_action="Treat as blend risk; confirm photocenter is on the target.",
        )
    if (isinstance(dil, (int, float)) and dil < DILUTION_UNCLEAR) or (
        isinstance(dmag, (int, float)) and dmag < BRIGHT_UNCLEAR_DMAG
    ):
        return ChecklistItem(
            id="neighbours",
            label="Neighbours / dilution (Gaia)",
            status="unclear",
            detail=detail + " — possible dilution.",
            next_action="Compare aperture vs Gaia offsets; wait for centroid if TPF exists.",
        )
    return ChecklistItem(
        id="neighbours",
        label="Neighbours / dilution (Gaia)",
        status="pass",
        detail=detail + ".",
        next_action="Continue with centroid / archive checks.",
    )


def _centroid_item(centroid: dict[str, Any] | None) -> ChecklistItem:
    payload = centroid if isinstance(centroid, dict) else None
    if not payload or payload.get("status") == "unavailable":
        reason = (payload or {}).get("reason") or "TPF centroid not run yet."
        return ChecklistItem(
            id="centroid",
            label="Centroid / aperture",
            status="unavailable",
            detail=f"Centroid unavailable ({reason}).",
            next_action="Skip for synthetic LCs; otherwise fetch one TPF and re-run vet_neighbours.",
        )
    pvalue = payload.get("pvalue")
    offset = payload.get("offset_pix")
    bits = []
    if isinstance(pvalue, (int, float)):
        bits.append(f"p={pvalue:.3g}")
    if isinstance(offset, (int, float)):
        bits.append(f"offset={offset:.3f} px")
    extra = f" ({', '.join(bits)})" if bits else ""
    if payload.get("status") == "fail" or payload.get("pass") is False:
        return ChecklistItem(
            id="centroid",
            label="Centroid / aperture",
            status="fail",
            detail=f"Significant in-transit photocenter shift{extra}.",
            next_action="Likely background eclipse / blend — reject or follow up off-target.",
        )
    if payload.get("status") == "pass" or payload.get("pass") is True:
        return ChecklistItem(
            id="centroid",
            label="Centroid / aperture",
            status="pass",
            detail=f"No significant centroid shift{extra}.",
            next_action="Centroid consistent with the target aperture.",
        )
    return ChecklistItem(
        id="centroid",
        label="Centroid / aperture",
        status="unclear",
        detail=f"Centroid result incomplete{extra}.",
        next_action="Re-run vet_neighbours with a readable TPF.",
    )


def _fpp_item(validation: dict[str, Any] | None) -> ChecklistItem:
    from projects.exoplanet.pipelines.validate import FPP_LIKELY, FPP_VALIDATED, NFPP_NEARBY_FP

    payload = validation if isinstance(validation, dict) else None
    if not payload or payload.get("status") == "unavailable":
        reason = (payload or {}).get("reason") or "not run yet"
        return ChecklistItem(
            id="fpp",
            label="Statistical validation (FPP)",
            status="unavailable",
            detail=f"FPP unavailable ({reason}).",
            next_action="Run validation from /review or /vet_validate <id> (EXOPLANET_TRICERATOPS=true).",
        )
    fpp = payload.get("fpp")
    nfpp = payload.get("nfpp")
    method = payload.get("method") or "unknown"
    bits = [f"method={method}"]
    if isinstance(fpp, (int, float)):
        bits.append(f"FPP={fpp:.3g}")
    if isinstance(nfpp, (int, float)):
        bits.append(f"NFPP={nfpp:.3g}")
    detail = "; ".join(bits)
    if isinstance(nfpp, (int, float)) and nfpp >= NFPP_NEARBY_FP:
        return ChecklistItem(
            id="fpp",
            label="Statistical validation (FPP)",
            status="fail",
            detail=detail + " — likely nearby false positive.",
            next_action="Do not validate; inspect neighbours/centroid and reject or follow up.",
        )
    if isinstance(fpp, (int, float)) and fpp >= FPP_LIKELY:
        return ChecklistItem(
            id="fpp",
            label="Statistical validation (FPP)",
            status="fail",
            detail=detail + " — FPP ≥ 0.5.",
            next_action="Treat as FP until new photometry/centroids say otherwise.",
        )
    if isinstance(fpp, (int, float)) and fpp >= FPP_VALIDATED:
        return ChecklistItem(
            id="fpp",
            label="Statistical validation (FPP)",
            status="unclear",
            detail=detail + " — not below the 0.015 validation cut.",
            next_action="Likely planet only if NFPP is tiny; need more data to validate.",
        )
    return ChecklistItem(
        id="fpp",
        label="Statistical validation (FPP)",
        status="pass",
        detail=detail + " — below Giacalone et al. FPP cut.",
        next_action="FPP is low; still confirm odd-even and centroid before approve.",
    )


def build_vetting_checklist(
    *,
    depth_ppm: float | None,
    odd_depth_ppm: float | None = None,
    even_depth_ppm: float | None = None,
    odd_even_delta_ppm: float | None = None,
    plots_ready: bool = False,
    available_plots: list[str] | None = None,
    neighbours: dict[str, Any] | None = None,
    centroid: dict[str, Any] | None = None,
    validation: dict[str, Any] | None = None,
) -> list[ChecklistItem]:
    items = [
        _depth_item(_f(depth_ppm)),
        _odd_even_item(_f(odd_depth_ppm), _f(even_depth_ppm), _f(odd_even_delta_ppm)),
        _plots_item(bool(plots_ready), available_plots),
        _neighbours_item(neighbours),
        _centroid_item(centroid),
        _fpp_item(validation),
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
    neighbours = getattr(obj, "neighbours", None)
    if not isinstance(neighbours, dict):
        neighbours = loads_payload(getattr(obj, "neighbours_json", None))
    centroid = getattr(obj, "centroid", None)
    if not isinstance(centroid, dict):
        centroid = loads_payload(getattr(obj, "centroid_json", None))
    validation = getattr(obj, "validation", None)
    if not isinstance(validation, dict):
        validation = loads_payload(getattr(obj, "validation_json", None))
    return build_vetting_checklist(
        depth_ppm=_f(getattr(obj, "depth_ppm", None)),
        odd_depth_ppm=_f(getattr(obj, "odd_depth_ppm", None)),
        even_depth_ppm=_f(getattr(obj, "even_depth_ppm", None)),
        odd_even_delta_ppm=_f(getattr(obj, "odd_even_delta_ppm", None)),
        plots_ready=bool(getattr(obj, "plots_ready", False)),
        available_plots=list(plots) if plots else None,
        neighbours=neighbours,
        centroid=centroid,
        validation=validation,
    )
