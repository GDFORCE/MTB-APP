"""Capability-based authorization for the UCTSM surfaces (P3).

``catalog``  - the typed, versioned capability catalog and PROPOSED role defaults.
``policy``   - the pure decision: ``authorize(principal, capability, resource)``.
``manifest`` - the declaration every registered UCTSM route must have.
``guard``    - FastAPI integration: resolves scope from persisted records, applies
               the policy, audits denials and fails closed.
``grants``   - where explicit capability assignments live (store protocol + fakes).
``maker_checker`` - segregation of material editing from approval.

Nothing in ``catalog``/``policy``/``maker_checker`` imports FastAPI or a driver.
"""
