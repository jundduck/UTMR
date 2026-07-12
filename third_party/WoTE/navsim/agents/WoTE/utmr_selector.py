from dataclasses import dataclass
from typing import Optional

import torch


@dataclass
class UTMRDiagnostics:
    selected_indices: torch.Tensor
    baseline_indices: torch.Tensor
    entropy: torch.Tensor
    margin: torch.Tensor
    triggered: torch.Tensor
    feasible_count: torch.Tensor
    rerank_accepted: torch.Tensor


def normalize_scores(scores: torch.Tensor, eps: float = 1e-8) -> torch.Tensor:
    min_scores = scores.min(dim=-1, keepdim=True).values
    max_scores = scores.max(dim=-1, keepdim=True).values
    return (scores - min_scores) / (max_scores - min_scores + eps)


def score_entropy_and_margin(scores: torch.Tensor, beta: float = 1.0) -> tuple[torch.Tensor, torch.Tensor]:
    num_candidates = scores.shape[-1]
    normalized = normalize_scores(scores)
    probabilities = torch.softmax(normalized / beta, dim=-1)
    entropy = -(probabilities * torch.log(probabilities.clamp_min(1e-12))).sum(dim=-1)
    entropy = entropy / torch.log(torch.tensor(float(num_candidates), device=scores.device, dtype=scores.dtype))
    top2 = torch.topk(normalized, k=2, dim=-1).values
    margin = top2[..., 0] - top2[..., 1]
    return entropy, margin


def select_with_utmr(
    coarse_scores: torch.Tensor,
    fine_scores: Optional[torch.Tensor] = None,
    feasible_mask: Optional[torch.Tensor] = None,
    beta: float = 1.0,
    gamma_h: float = 0.75,
    gamma_m: float = 0.05,
    top_n: int = 8,
    uniform_fine: bool = False,
    fine_margin_min: float = 0.0,
    max_coarse_drop: float = float("inf"),
) -> UTMRDiagnostics:
    if feasible_mask is None:
        feasible_mask = torch.ones_like(coarse_scores, dtype=torch.bool)

    safe_coarse = coarse_scores.masked_fill(~feasible_mask, -torch.inf)
    baseline_indices = torch.argmax(safe_coarse, dim=-1)
    entropy, margin = score_entropy_and_margin(coarse_scores, beta=beta)
    triggered = (entropy > gamma_h) | (margin < gamma_m)
    if uniform_fine:
        triggered = torch.ones_like(triggered, dtype=torch.bool)

    selected_indices = baseline_indices.clone()
    rerank_accepted = torch.zeros_like(triggered, dtype=torch.bool)
    if fine_scores is not None:
        top_n = min(top_n, coarse_scores.shape[-1])
        top_indices = torch.topk(safe_coarse, k=top_n, dim=-1).indices
        candidate_fine = fine_scores.gather(dim=-1, index=top_indices)
        candidate_feasible = feasible_mask.gather(dim=-1, index=top_indices)
        candidate_fine = candidate_fine.masked_fill(~candidate_feasible, -torch.inf)
        reranked = top_indices.gather(dim=-1, index=torch.argmax(candidate_fine, dim=-1, keepdim=True)).squeeze(-1)
        baseline_fine = fine_scores.gather(dim=-1, index=baseline_indices.unsqueeze(-1)).squeeze(-1)
        reranked_fine = fine_scores.gather(dim=-1, index=reranked.unsqueeze(-1)).squeeze(-1)
        baseline_coarse = safe_coarse.gather(dim=-1, index=baseline_indices.unsqueeze(-1)).squeeze(-1)
        reranked_coarse = safe_coarse.gather(dim=-1, index=reranked.unsqueeze(-1)).squeeze(-1)
        fine_improved = reranked_fine >= baseline_fine + fine_margin_min
        coarse_drop_ok = (baseline_coarse - reranked_coarse) <= max_coarse_drop
        rerank_accepted = triggered & fine_improved & coarse_drop_ok
        selected_indices = torch.where(rerank_accepted, reranked, baseline_indices)

    feasible_count = feasible_mask.sum(dim=-1)
    return UTMRDiagnostics(
        selected_indices=selected_indices,
        baseline_indices=baseline_indices,
        entropy=entropy,
        margin=margin,
        triggered=triggered,
        feasible_count=feasible_count,
        rerank_accepted=rerank_accepted,
    )
