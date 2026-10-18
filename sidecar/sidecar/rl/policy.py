"""Masked GRU policy, behavioral cloning, and clipped PPO.

NumPy only, so a smoke run fits in a CPU test. The mask uses a large negative
logit on illegal quote actions, the same device as the Figgie policy this module
is transferring from.
"""

from __future__ import annotations

import numpy as np

from sidecar.rl.sim import unwind_weight


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


class Layout:
    def __init__(self, input_size: int, hidden: int, n_actions: int) -> None:
        self.slices: dict[str, tuple[int, int, tuple[int, ...]]] = {}
        cursor = 0

        def take(name: str, shape: tuple[int, ...]) -> None:
            nonlocal cursor
            size = 1
            for dim in shape:
                size *= dim
            self.slices[name] = (cursor, cursor + size, shape)
            cursor += size

        for gate in ("z", "r", "n"):
            take(f"W_x{gate}", (hidden, input_size))
            take(f"W_h{gate}", (hidden, hidden))
            take(f"b_{gate}", (hidden,))
        take("W_o", (n_actions, hidden))
        take("b_o", (n_actions,))
        take("W_v", (hidden,))
        take("b_v", (1,))
        self.n = cursor

    def view(self, theta: np.ndarray, name: str) -> np.ndarray:
        start, stop, shape = self.slices[name]
        return theta[start:stop].reshape(shape)


class Adam:
    def __init__(self, n: int, lr: float) -> None:
        self.lr = lr
        self.m = np.zeros(n)
        self.v = np.zeros(n)
        self.t = 0
        self.b1 = 0.9
        self.b2 = 0.999

    def step(self, theta: np.ndarray, grad: np.ndarray) -> None:
        self.t += 1
        self.m = self.b1 * self.m + (1.0 - self.b1) * grad
        self.v = self.b2 * self.v + (1.0 - self.b2) * (grad * grad)
        mhat = self.m / (1.0 - self.b1**self.t)
        vhat = self.v / (1.0 - self.b2**self.t)
        theta -= self.lr * mhat / (np.sqrt(vhat) + 1.0e-8)


class GRUPolicy:
    def __init__(self, input_size: int, hidden: int, n_actions: int, rng: np.random.Generator) -> None:
        self.input_size = input_size
        self.hidden = hidden
        self.n_actions = n_actions
        self.layout = Layout(input_size, hidden, n_actions)
        self.theta = rng.normal(0.0, 0.05, size=self.layout.n)
        self.adam = Adam(self.layout.n, lr=1.0e-2)

    def copy_theta(self) -> np.ndarray:
        return self.theta.copy()

    def load_theta(self, theta: np.ndarray) -> None:
        self.theta = theta.copy()

    def _step(self, h: np.ndarray, x: np.ndarray, theta: np.ndarray | None = None):
        theta = self.theta if theta is None else theta
        view = self.layout.view
        xz = view(theta, "W_xz") @ x + view(theta, "W_hz") @ h + view(theta, "b_z")
        xr = view(theta, "W_xr") @ x + view(theta, "W_hr") @ h + view(theta, "b_r")
        z = _sigmoid(xz)
        r = _sigmoid(xr)
        xn = view(theta, "W_xn") @ x + view(theta, "W_hn") @ (r * h) + view(theta, "b_n")
        n = np.tanh(xn)
        h_new = (1.0 - z) * h + z * n
        cache = {"x": x, "h": h, "z": z, "r": r, "n": n, "h_new": h_new}
        return h_new, cache

    def forward_sequence(self, observations: np.ndarray, theta: np.ndarray | None = None):
        theta = self.theta if theta is None else theta
        h = np.zeros(self.hidden)
        logits = []
        values = []
        caches = []
        for observation in observations:
            h, cache = self._step(h, observation, theta)
            logit = self.layout.view(theta, "W_o") @ h + self.layout.view(theta, "b_o")
            value = float(np.dot(self.layout.view(theta, "W_v"), h) + self.layout.view(theta, "b_v")[0])
            caches.append(cache)
            logits.append(logit)
            values.append(value)
        return logits, np.asarray(values, dtype=np.float64), caches

    def backward_sequence(
        self,
        caches: list[dict],
        dlogits: list[np.ndarray],
        dvalues: np.ndarray,
        theta: np.ndarray | None = None,
    ) -> np.ndarray:
        theta = self.theta if theta is None else theta
        grad = np.zeros_like(theta)
        dh = np.zeros(self.hidden)
        view = self.layout.view
        for cache, dlogit, dvalue in zip(reversed(caches), reversed(dlogits), reversed(dvalues), strict=True):
            h_new = cache["h_new"]
            dh = dh + view(theta, "W_o").T @ dlogit
            dh = dh + view(theta, "W_v") * float(dvalue)
            start, stop, _shape = self.layout.slices["W_o"]
            grad[start:stop] += np.outer(dlogit, h_new).reshape(-1)
            start, stop, _shape = self.layout.slices["b_o"]
            grad[start:stop] += dlogit.reshape(-1)
            start, stop, _shape = self.layout.slices["W_v"]
            grad[start:stop] += (float(dvalue) * h_new).reshape(-1)
            start, stop, _shape = self.layout.slices["b_v"]
            grad[start:stop] += float(dvalue)
            dh = self._gru_backward(cache, dh, theta, grad)
        return grad

    def _gru_backward(self, cache: dict, dh_new: np.ndarray, theta: np.ndarray, grad: np.ndarray) -> np.ndarray:
        x = cache["x"]
        h = cache["h"]
        z = cache["z"]
        r = cache["r"]
        n = cache["n"]
        view = self.layout.view
        dz = (n - h) * dh_new
        dn = z * dh_new
        dh = (1.0 - z) * dh_new
        dn_pre = (1.0 - n * n) * dn
        d_rh = view(theta, "W_hn").T @ dn_pre
        dr = d_rh * h
        dh = dh + d_rh * r
        dz_pre = z * (1.0 - z) * dz
        dr_pre = r * (1.0 - r) * dr
        dh = dh + view(theta, "W_hz").T @ dz_pre
        dh = dh + view(theta, "W_hr").T @ dr_pre

        def add(name: str, value: np.ndarray) -> None:
            start, stop, _shape = self.layout.slices[name]
            grad[start:stop] += value.reshape(-1)

        add("W_xz", np.outer(dz_pre, x))
        add("W_hz", np.outer(dz_pre, h))
        add("b_z", dz_pre)
        add("W_xr", np.outer(dr_pre, x))
        add("W_hr", np.outer(dr_pre, h))
        add("b_r", dr_pre)
        add("W_xn", np.outer(dn_pre, x))
        add("W_hn", np.outer(dn_pre, r * h))
        add("b_n", dn_pre)
        return dh

    def distribution(self, logits: np.ndarray, mask: np.ndarray) -> np.ndarray:
        scores = logits + mask
        scores = scores - np.max(scores)
        probs = np.exp(scores)
        total = probs.sum()
        if not np.isfinite(total) or total <= 0:
            legal = mask > -1.0e8
            probs = legal.astype(np.float64)
            total = probs.sum()
        return probs / total

    def sample(self, logits: np.ndarray, mask: np.ndarray, rng: np.random.Generator) -> tuple[int, float]:
        probs = self.distribution(logits, mask)
        action = int(rng.choice(self.n_actions, p=probs))
        return action, float(np.log(probs[action] + 1.0e-12))

    def greedy(self, logits: np.ndarray, mask: np.ndarray) -> int:
        scores = logits + mask
        return int(np.argmax(scores))


def masked_logp(logits: np.ndarray, mask: np.ndarray, action: int) -> tuple[float, np.ndarray]:
    scores = logits + mask
    scores = scores - np.max(scores)
    probs = np.exp(scores)
    probs = probs / probs.sum()
    logp = float(np.log(probs[action] + 1.0e-12))
    onehot = np.zeros_like(probs)
    onehot[action] = 1.0
    dlogp = onehot - probs
    return logp, dlogp


def bc_loss_and_grad(
    policy: GRUPolicy,
    observations: np.ndarray,
    actions: np.ndarray,
    masks: np.ndarray,
    inventories: np.ndarray,
) -> tuple[float, np.ndarray]:
    logits, _values, caches = policy.forward_sequence(observations)
    dlogits = []
    total = 0.0
    weight_sum = 0.0
    for logit, action, mask, inventory in zip(logits, actions, masks, inventories, strict=True):
        logp, dlogp = masked_logp(logit, mask, int(action))
        weight = unwind_weight(int(inventory), int(action))
        total += weight * (-logp)
        weight_sum += weight
        dlogits.append(weight * (-dlogp))
    scale = 1.0 / max(weight_sum, 1.0)
    total *= scale
    dlogits = [item * scale for item in dlogits]
    grad = policy.backward_sequence(caches, dlogits, np.zeros(len(actions)))
    return total, grad


def gae(rewards: np.ndarray, values: np.ndarray, gamma: float = 0.99, lam: float = 0.95):
    length = len(rewards)
    advantages = np.zeros(length)
    last = 0.0
    for index in range(length - 1, -1, -1):
        next_value = values[index + 1] if index + 1 < length else 0.0
        delta = rewards[index] + gamma * next_value - values[index]
        last = delta + gamma * lam * last
        advantages[index] = last
    returns = advantages + values
    return advantages, returns


def ppo_grads(
    policy: GRUPolicy,
    observations: np.ndarray,
    actions: np.ndarray,
    old_logp: np.ndarray,
    rewards: np.ndarray,
    masks: np.ndarray,
    *,
    clip: float = 0.2,
    value_coef: float = 0.5,
    entropy_coef: float = 0.01,
) -> tuple[np.ndarray, float]:
    logits, values, caches = policy.forward_sequence(observations)
    advantages, returns = gae(rewards, values)
    if advantages.std() > 1.0e-8:
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1.0e-8)
    dlogits = []
    loss = 0.0
    for index, logit in enumerate(logits):
        logp, dlogp = masked_logp(logit, masks[index], int(actions[index]))
        ratio = float(np.exp(np.clip(logp - old_logp[index], -20.0, 20.0)))
        unclipped = ratio * advantages[index]
        clipped = float(np.clip(ratio, 1.0 - clip, 1.0 + clip)) * advantages[index]
        if unclipped <= clipped:
            d_logp = -advantages[index] * ratio
        else:
            d_logp = 0.0
        probs = policy.distribution(logit, masks[index])
        entropy = float(-np.sum(probs * np.log(probs + 1.0e-12)))
        # dH/dlogit for a masked softmax is approximately -p * (log p + H), ignoring the
        # mask's hard zero. The finite-difference test covers the surrogate and the value
        # head; entropy is a small bonus and uses this standard softmax gradient.
        d_entropy = -probs * (np.log(probs + 1.0e-12) + entropy)
        dlogits.append(d_logp * dlogp - entropy_coef * d_entropy)
        value_error = values[index] - returns[index]
        loss += -min(unclipped, clipped) - entropy_coef * entropy + value_coef * value_error**2
    dvalues = value_coef * 2.0 * (values - returns)
    grad = policy.backward_sequence(caches, dlogits, dvalues)
    return grad, float(loss / len(actions))


class OpponentPool:
    """Prioritized fictitious self-play. Weaker snapshots are sampled more often."""

    def __init__(self, limit: int = 4) -> None:
        self.limit = limit
        self.snapshots: list[list] = []

    def add(self, theta: np.ndarray) -> None:
        self.snapshots.append([theta.copy(), 0.5])
        if len(self.snapshots) > self.limit:
            self.snapshots.pop(0)

    def sample(self, rng: np.random.Generator, psp: float = 0.3) -> int | None:
        if not self.snapshots or float(rng.random()) < psp:
            return None
        weights = np.array([(max(0.0, 1.0 - win)) ** 2 for _theta, win in self.snapshots], dtype=np.float64)
        if float(weights.sum()) <= 0:
            weights[:] = 1.0
        probs = weights / weights.sum()
        return int(rng.choice(len(self.snapshots), p=probs))

    def update(self, index: int | None, win: bool) -> None:
        if index is None:
            return
        previous = self.snapshots[index][1]
        self.snapshots[index][1] = 0.9 * previous + 0.1 * (1.0 if win else 0.0)
