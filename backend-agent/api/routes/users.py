"""Reserved for future profile reads.

Login and signup live in ``auth.py``. Language and consent live in
``language.py`` and ``memory.py``. This module does not invent a second
user-profile API.
"""

from fastapi import APIRouter

router = APIRouter(tags=["users"])
