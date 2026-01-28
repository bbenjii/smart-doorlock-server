from pydantic import BaseModel
from typing import Any, List, Literal

FIRESTORE_OPS = Literal[
    "==",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not-in",
    "array-contains",
    "array-contains-any",
]


class Filter(BaseModel):
    key: str
    value: Any
    op: Literal[FIRESTORE_OPS] = "=="

