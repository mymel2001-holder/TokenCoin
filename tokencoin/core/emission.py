"""
TokenCoin Emission Curve
========================
Implements the "fair, unbiased printing" monetary policy for TokenCoin.

Instead of Bitcoin's discrete halving events (which create sudden reward drops
and favor early miners), TokenCoin uses a smooth exponential decay emission
curve with a tail emission floor. This ensures:

  1. **Fair distribution** — No abrupt halving events that create miner timing
     advantages. Rewards decrease smoothly and predictably.
  2. **Never runs out** — The supply asymptotically approaches the max supply,
     and a tail emission floor ensures mining is always rewarded.
  3. **Unbiased printing** — Every block has a deterministically calculated
     reward based on the current supply, not on discrete epoch boundaries.

Emission Formula:
-----------------
    block_reward(n) = max(tail_emission, remaining_supply × decay_factor)

Where:
    - remaining_supply = max_supply - current_circulating_supply
    - decay_factor = initial_block_reward / (max_supply - base_supply)
    - tail_emission = minimum reward floor (in atomic units)

The decay_factor is calibrated so that the first block after the base supply
earns exactly `initial_block_reward`. As the remaining supply shrinks, the
reward smoothly decreases until it hits the tail emission floor, where it
stays constant forever.

Reference:
    Monero's smooth emission curve (CryptoNote protocol)
    https://www.getmonero.org/resources/moneropedia/emission.html
"""

import math
import logging
from dataclasses import dataclass
from typing import Tuple

from tokencoin.config import CONFIG

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Atomic Units
# ---------------------------------------------------------------------------
# TokenCoin uses 9 decimal places (like Monero)
# 1 TKC = 1_000_000_000 atomic units (nanoTKC)
TKC_DECIMALS = 9
ATOMIC_UNITS_PER_TKC = 10 ** TKC_DECIMALS  # 1_000_000_000


def tkc_to_atomic(amount_tkc: float) -> int:
    """Convert TKC (float) to atomic units (int)."""
    return int(round(amount_tkc * ATOMIC_UNITS_PER_TKC))


def atomic_to_tkc(amount_atomic: int) -> float:
    """Convert atomic units (int) to TKC (float)."""
    return amount_atomic / ATOMIC_UNITS_PER_TKC


# ---------------------------------------------------------------------------
# Emission Curve Calculator
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmissionParameters:
    """
    Emission curve parameters defining TokenCoin's monetary policy.

    These parameters are designed to produce a smooth, asymptotic emission
    curve that never quite reaches the max supply, ensuring mining rewards
    are always available (fair, unbiased printing).
    """
    # Maximum total supply in atomic units (10 Trillion TKC)
    max_supply_atomic: int

    # Base (pre-mined) supply in atomic units (6.4 Billion TKC)
    base_supply_atomic: int

    # Initial block reward in atomic units (12 TKC)
    initial_reward_atomic: int

    # Tail emission floor in atomic units — the minimum reward per block
    # Set to 0.1 TKC by default, ensuring mining is always worthwhile
    tail_emission_atomic: int

    # Decay factor — calibrated so reward(0) = initial_reward
    # decay = initial_reward / (max_supply - base_supply)
    decay_factor: float

    @classmethod
    def from_config(cls) -> "EmissionParameters":
        """Build emission parameters from the global CONFIG."""
        monetary = CONFIG.monetary

        max_supply_atomic = monetary.max_supply * ATOMIC_UNITS_PER_TKC
        base_supply_atomic = monetary.base_supply * ATOMIC_UNITS_PER_TKC
        initial_reward_atomic = monetary.initial_block_reward * ATOMIC_UNITS_PER_TKC
        tail_emission_atomic = monetary.tail_emission * ATOMIC_UNITS_PER_TKC

        # Calculate decay factor so that the first block reward equals initial_reward
        # reward(0) = (max_supply - base_supply) * decay_factor = initial_reward
        # => decay_factor = initial_reward / (max_supply - base_supply)
        remaining_at_genesis = max_supply_atomic - base_supply_atomic
        if remaining_at_genesis <= 0:
            decay_factor = 0.0
        else:
            decay_factor = initial_reward_atomic / remaining_at_genesis

        return cls(
            max_supply_atomic=max_supply_atomic,
            base_supply_atomic=base_supply_atomic,
            initial_reward_atomic=initial_reward_atomic,
            tail_emission_atomic=tail_emission_atomic,
            decay_factor=decay_factor,
        )

    def to_dict(self) -> dict:
        """Serialize parameters to a dictionary."""
        return {
            "max_supply_tkc": atomic_to_tkc(self.max_supply_atomic),
            "base_supply_tkc": atomic_to_tkc(self.base_supply_atomic),
            "initial_reward_tkc": atomic_to_tkc(self.initial_reward_atomic),
            "tail_emission_tkc": atomic_to_tkc(self.tail_emission_atomic),
            "decay_factor": self.decay_factor,
        }


class EmissionCurve:
    """
    TokenCoin's smooth emission curve calculator.

    This implements the "fair, unbiased printing" mechanism described in
    the TokenCoin design document. The curve is:

    - **Smooth**: No discrete halving events. Rewards decrease continuously.
    - **Asymptotic**: The supply approaches max_supply but never reaches it.
    - **Tail emission**: A minimum reward floor ensures mining is always viable.

    Usage:
        curve = EmissionCurve()
        reward = curve.block_reward(current_circulating_supply)
        supply_after = curve.supply_after_block(current_circulating_supply)
    """

    def __init__(self, params: EmissionParameters = None):
        self.params = params or EmissionParameters.from_config()

    # ------------------------------------------------------------------
    # Core Reward Calculation
    # ------------------------------------------------------------------

    def block_reward(self, current_supply_atomic: int) -> int:
        """
        Calculate the block reward for the next block given the current
        circulating supply.

        The reward is the maximum of:
          1. The tail emission floor (minimum guaranteed reward)
          2. The exponential decay reward: remaining_supply × decay_factor

        This ensures the reward smoothly decreases but never goes below
        the tail emission, providing "fair, unbiased printing" forever.
        """
        if current_supply_atomic >= self.params.max_supply_atomic:
            return self.params.tail_emission_atomic

        remaining = self.params.max_supply_atomic - current_supply_atomic

        # Exponential decay reward
        decay_reward = int(remaining * self.params.decay_factor)

        # Never go below tail emission
        reward = max(self.params.tail_emission_atomic, decay_reward)

        # Cap at initial reward (safety check)
        reward = min(reward, self.params.initial_reward_atomic)

        # Ensure we don't exceed remaining supply
        reward = min(reward, remaining)

        return reward

    def supply_after_block(self, current_supply_atomic: int) -> int:
        """Calculate the total supply after mining one block."""
        reward = self.block_reward(current_supply_atomic)
        return current_supply_atomic + reward

    def supply_after_blocks(self, current_supply_atomic: int,
                            num_blocks: int) -> int:
        """
        Simulate the supply after N blocks. Useful for estimating future
        supply and reward rates.
        """
        supply = current_supply_atomic
        for i in range(num_blocks):
            reward = self.block_reward(supply)
            if reward <= self.params.tail_emission_atomic:
                # Once at tail emission, it's linear for the remaining blocks
                remaining = num_blocks - i
                supply += reward * remaining
                break
            supply += reward
        return supply

    # ------------------------------------------------------------------
    # Analytical Queries
    # ------------------------------------------------------------------

    def reward_at_block_height(self, block_height: int) -> int:
        """
        Calculate the block reward at a given block height, assuming
        the chain started from the base supply.

        This is an analytical approximation that simulates the emission
        from genesis to the given height.
        """
        supply = self.params.base_supply_atomic
        for _ in range(block_height):
            reward = self.block_reward(supply)
            supply += reward
            if reward <= self.params.tail_emission_atomic:
                # Once at tail emission, reward stays constant
                return reward
        return self.block_reward(supply)

    def blocks_until_tail_emission(self) -> int:
        """
        Estimate how many blocks until the reward reaches the tail emission
        floor. This is approximate since the decay is continuous.
        """
        supply = self.params.base_supply_atomic
        blocks = 0
        while True:
            reward = self.block_reward(supply)
            if reward <= self.params.tail_emission_atomic:
                return blocks
            supply += reward
            blocks += 1
            if blocks > 10_000_000_000:  # Safety limit
                return blocks

    def supply_at_block_height(self, block_height: int) -> int:
        """Estimate the total supply at a given block height."""
        return self.supply_after_blocks(
            self.params.base_supply_atomic, block_height
        )

    def emission_rate(self, current_supply_atomic: int) -> float:
        """
        Calculate the annual emission rate (inflation) as a percentage.
        Based on current block reward and block time.
        """
        reward = self.block_reward(current_supply_atomic)
        blocks_per_year = (365.25 * 24 * 3600) / CONFIG.monetary.block_time_seconds
        annual_emission = reward * blocks_per_year
        if current_supply_atomic > 0:
            return (annual_emission / current_supply_atomic) * 100.0
        return 0.0

    # ------------------------------------------------------------------
    # Summary & Reporting
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Generate a human-readable summary of the emission curve state."""
        params = self.params
        return {
            "parameters": params.to_dict(),
            "max_supply_tkc": atomic_to_tkc(params.max_supply_atomic),
            "base_supply_tkc": atomic_to_tkc(params.base_supply_atomic),
            "remaining_to_mine_tkc": atomic_to_tkc(
                params.max_supply_atomic - params.base_supply_atomic
            ),
            "initial_reward_tkc": atomic_to_tkc(params.initial_reward_atomic),
            "tail_emission_tkc": atomic_to_tkc(params.tail_emission_atomic),
            "decay_factor": params.decay_factor,
        }

    def emission_schedule(self, num_samples: int = 100) -> list:
        """
        Generate a sample emission schedule for visualization/analysis.
        Returns a list of dicts with block_height, reward_tkc, supply_tkc.
        """
        supply = self.params.base_supply_atomic
        schedule = []

        # Sample adaptively to show the curve shape
        block = 0
        while block < 10_000_000 and len(schedule) < num_samples:
            reward = self.block_reward(supply)
            schedule.append({
                "block_height": block,
                "reward_tkc": atomic_to_tkc(reward),
                "supply_tkc": atomic_to_tkc(supply),
            })

            if reward <= self.params.tail_emission_atomic:
                # Once at tail emission, we can stop
                break

            # Adaptive step size for sampling — advance supply by `step` blocks
            if block < 1000:
                step = 1
            elif block < 100_000:
                step = 100
            elif block < 1_000_000:
                step = 1_000
            else:
                step = 10_000

            supply = self.supply_after_blocks(supply, step)
            block += step

        return schedule


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------

def get_block_reward(current_supply_atomic: int) -> int:
    """
    Convenience function to get the block reward for the next block.

    Args:
        current_supply_atomic: Current circulating supply in atomic units.

    Returns:
        Block reward in atomic units.
    """
    curve = EmissionCurve()
    return curve.block_reward(current_supply_atomic)


def get_supply_after_block(current_supply_atomic: int) -> int:
    """Convenience function to get supply after mining one block."""
    curve = EmissionCurve()
    return curve.supply_after_block(current_supply_atomic)


def format_supply_info(current_supply_atomic: int) -> str:
    """Format a human-readable supply status string."""
    curve = EmissionCurve()
    reward = curve.block_reward(current_supply_atomic)
    rate = curve.emission_rate(current_supply_atomic)
    remaining = curve.params.max_supply_atomic - current_supply_atomic

    return (
        f"Supply: {atomic_to_tkc(current_supply_atomic):,.2f} / "
        f"{atomic_to_tkc(curve.params.max_supply_atomic):,.0f} TKC\n"
        f"Block Reward: {atomic_to_tkc(reward):,.4f} TKC\n"
        f"Remaining to Mine: {atomic_to_tkc(remaining):,.2f} TKC\n"
        f"Annual Inflation: {rate:.4f}%"
    )
