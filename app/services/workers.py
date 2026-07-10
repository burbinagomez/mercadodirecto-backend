"""Cron-entrypoint for background payment workers.

Usage from cron (every 10 minutes)::

    python -m app.services.workers --run-payout-retry --run-pse-cleanup

Or via the installed ``md-workers`` CLI::

    md-workers --run-payout-retry --run-pse-cleanup

Each worker is idempotent and safe to run on overlapping schedules.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Ensure the app package is on the path when called directly.
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("workers")


def _get_db() -> "Session":
    """Create a standalone DB session (no FastAPI dependency)."""
    from app.core.database import SessionLocal

    return SessionLocal()


def run_payout_retry() -> int:
    """Retry pending/failed FarmerPayout records."""
    from app.services.payout_worker import retry_pending_payouts

    db = _get_db()
    try:
        return retry_pending_payouts(db)
    finally:
        db.close()


def run_pse_cleanup() -> int:
    """Expire abandoned PSE (Mono) collection intents."""
    from app.services.pse_cleanup import expire_abandoned_intents

    db = _get_db()
    try:
        return expire_abandoned_intents(db)
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MercadoDirecto payment workers")
    parser.add_argument(
        "--run-payout-retry",
        action="store_true",
        help="Retry pending/failed farmer payouts",
    )
    parser.add_argument(
        "--run-pse-cleanup",
        action="store_true",
        help="Expire abandoned PSE collection intents",
    )
    args = parser.parse_args()

    if not args.run_payout_retry and not args.run_pse_cleanup:
        parser.print_help()
        sys.exit(0)

    if args.run_payout_retry:
        logger.info("=== Payout retry worker ===")
        count = run_payout_retry()
        logger.info("Payout retry worker finished: %d processed.", count)

    if args.run_pse_cleanup:
        logger.info("=== PSE cleanup worker ===")
        count = run_pse_cleanup()
        logger.info("PSE cleanup worker finished: %d expired.", count)


if __name__ == "__main__":
    main()
