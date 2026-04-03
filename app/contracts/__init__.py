"""
contracts — Architecture contract layer.

Typed events, commands, and state documents that define how subsystems interact.
Formalizes the protocol that currently exists implicitly across code and Firestore
collection conventions.

This is the "architecture contract" recommended by the external review:
a small set of typed definitions that subsystems can depend on without coupling
to each other's internals.
"""
