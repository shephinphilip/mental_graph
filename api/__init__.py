"""HTTP API layer.

Route *declarations* live here; business logic stays in the domain modules
(``sleep/``, ``journaling/``, ``tasks/``, ``services/`` …). Routes are thin:
authenticate, validate, delegate, shape the response.
"""
