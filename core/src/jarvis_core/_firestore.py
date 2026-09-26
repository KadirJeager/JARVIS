"""Shared Firestore settings."""

# Transactions on a contended document (one turn claimed by duplicate task
# deliveries, one memory file written concurrently) are aborted and retried by
# the client. Its default of 5 attempts can be exhausted under load, which
# surfaces as a failed request even though nothing was applied; 20 attempts
# keeps such races resolving to their normal outcome (a conflict or `None`).
TRANSACTION_ATTEMPTS = 20
