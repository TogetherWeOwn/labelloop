"""Shared effective audit-write capability query for both database adapters."""

AUDIT_REWRITE_QUERY = (
    "SELECT (has_table_privilege(current_user, 'canonical_event', 'UPDATE,DELETE,TRUNCATE')"
    " OR has_any_column_privilege(current_user, 'canonical_event', 'UPDATE')) AS broad"
)
