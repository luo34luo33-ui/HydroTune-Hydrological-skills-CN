"""Censored-Gaussian predictive BMA; parameters are on the normalized scale."""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq, minimize
from scipy.special import logsumexp
from scipy.stats import norm

SIGMA_FLOOR = 1e-6


def component_loglik(y, x, theta):
    a, b, sigma = theta
    mu = a + b * x
    return np.where(y == 0, norm.logcdf(-mu / sigma), norm.logpdf(y, mu, sigma))


def _objective(theta, y, x, r):
    a, b, sigma = theta
    mu = a + b * x
    zero = y == 0
    ll = component_loglik(y, x, theta)
    dmu = (y - mu) / sigma**2
    dsigma = -1 / sigma + (y - mu)**2 / sigma**3
    z = -mu[zero] / sigma
    mills = np.exp(norm.logpdf(z) - norm.logcdf(z))
    dmu[zero] = -mills / sigma
    dsigma[zero] = mills * mu[zero] / sigma**2
    return -np.dot(r, ll), -np.array([np.dot(r, dmu), np.dot(r, dmu * x), np.dot(r, dsigma)])


def fit(y, x, random_state, n_starts=5, max_iter=500, tol=1e-8):
    """Return best converged generalized-EM fit and per-start diagnostics."""
    y, x = np.asarray(y, float), np.asarray(x, float)
    if x.ndim != 2 or len(y) != len(x) or x.shape[1] < 2:
        raise ValueError("fit requires at least two members and matching observations")
    if not np.isfinite(x).all() or not np.isfinite(y).all() or (x < 0).any() or (y < 0).any():
        raise ValueError("fit requires finite nonnegative discharge")
    if len(y) < 2 or np.ptp(y) == 0:
        raise ValueError("training observations must vary")
    if n_starts < 1 or max_iter < 1 or not np.isfinite(tol) or tol <= 0:
        raise ValueError("invalid optimizer settings")
    scale = float(max(y.max(), x.max()))
    y, x = y / scale, x / scale
    rng = np.random.default_rng(random_state)
    k = x.shape[1]
    initial = []
    for col in x.T:
        slope = max(0.0, float(np.cov(col, y, ddof=0)[0, 1] / np.var(col))) if np.var(col) > 0 else 0.0
        intercept = float(y.mean() - slope * col.mean())
        initial.append([intercept, slope, max(float(np.std(y - intercept - slope * col)), 0.01)])
    diagnostics, best = [], None
    for start in range(n_starts):
        theta = np.array(initial)
        weights = np.full(k, 1 / k) if start == 0 else rng.dirichlet(np.ones(k))
        if start:
            theta[:, 0] += rng.normal(0, 0.05, k)
            theta[:, 1] *= np.exp(rng.normal(0, 0.2, k))
            theta[:, 2] *= np.exp(rng.normal(0, 0.2, k))
        def likelihood(t, w):
            logs = np.column_stack([component_loglik(y, x[:, j], t[j]) for j in range(k)])
            joint = logs + np.log(np.maximum(w, np.finfo(float).tiny))
            return joint, logsumexp(joint, axis=1)
        joint, totals = likelihood(theta, weights)
        history = [float(totals.mean())]
        converged, reason = False, "maximum_iterations"
        for iteration in range(1, max_iter + 1):
            responsibility = np.exp(joint - totals[:, None])
            candidate_w = responsibility.mean(axis=0)
            candidate_t = theta.copy()
            valid = True
            for j in range(k):
                r = responsibility[:, j]
                if r.sum() < np.finfo(float).eps:
                    continue
                result = minimize(_objective, theta[j], args=(y, x[:, j], r), jac=True,
                                  method="L-BFGS-B", bounds=[(None, None), (0, None), (SIGMA_FLOOR, None)],
                                  options={"maxiter": 500, "ftol": 1e-12, "gtol": 1e-8})
                old = _objective(theta[j], y, x[:, j], r)[0]
                if not np.isfinite(result.fun) or result.fun > old + 1e-9 * max(1, abs(old)):
                    valid = False
                    break
                candidate_t[j] = result.x
            if not valid:
                reason = "invalid_m_step"
                break
            candidate_joint, candidate_totals = likelihood(candidate_t, candidate_w)
            score = float(candidate_totals.mean())
            delta = score - history[-1]
            if not np.isfinite(score) or delta < -1e-10 * max(1, abs(history[-1])):
                reason = "likelihood_decreased"
                break
            theta, weights, joint, totals = candidate_t, candidate_w, candidate_joint, candidate_totals
            history.append(score)
            if abs(delta) <= tol * max(1, abs(history[-2])):
                converged, reason = True, "converged"
                break
        record = {"start": start, "converged": converged, "reason": reason,
                  "iterations": iteration, "mean_loglik_normalized": history[-1], "history": history}
        diagnostics.append(record)
        if converged and (best is None or history[-1] > best["score"]):
            best = {"weights": weights.tolist(), "theta": theta.tolist(), "scale_m3_s": scale,
                    "score": history[-1], "selected_start": start}
    return best, {"starts": diagnostics, "sigma_floor_normalized": SIGMA_FLOOR,
                  "settings": {"random_state": int(random_state), "n_starts": n_starts, "max_iter": max_iter, "tol": tol,
                               "m_step": {"method": "L-BFGS-B", "maxiter": 500, "ftol": 1e-12, "gtol": 1e-8},
                               "initialization": {"residual_sigma_floor": 0.01, "intercept_jitter_sd": 0.05,
                                                  "slope_sigma_log_jitter_sd": 0.2, "weight_dirichlet_alpha": 1}}}


def cdf(q, mu, sigma, weights):
    if q < 0:
        return 0.0
    return float(np.dot(weights, norm.cdf((q - mu) / sigma)))


def summarize(x, model, probabilities):
    x = np.asarray(x, float)
    t = np.array(model["theta"], float)
    weights = np.array(model["weights"], float)
    scale = model["scale_m3_s"]
    mu = (t[:, 0] + t[:, 1] * (x / scale)) * scale
    sigma = t[:, 2] * scale
    component_zero = norm.cdf(-mu / sigma)
    mean = float(np.dot(weights, sigma * norm.pdf(mu / sigma) + mu * norm.cdf(mu / sigma)))
    zero = float(np.dot(weights, component_zero))
    quantiles = []
    for p in probabilities:
        if p <= zero:
            quantiles.append(0.0)
            continue
        # Each component CDF is at least p at this upper bound.
        upper = max(float(np.max(mu + sigma * norm.ppf(p))), float(sigma.max()), 1e-12)
        upper += max(float(sigma.max()), scale) * 1e-10
        while cdf(upper, mu, sigma, weights) < p:
            upper *= 2
            if not np.isfinite(upper):
                raise ValueError("quantile bracket overflow")
        quantiles.append(float(brentq(lambda q: cdf(q, mu, sigma, weights) - p,
                                      0, upper, xtol=max(scale * 1e-12, 1e-12))))
    return mean, zero, quantiles, mu, sigma, component_zero
