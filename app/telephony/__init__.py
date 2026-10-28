"""Telephony adapter layer — provider-agnostic interface + Exotel implementation."""

from app.telephony.adapter import CallInfo, TelephonyAdapter
from app.telephony.base import BaseTelephonyProvider

__all__ = [
    "BaseTelephonyProvider",
    "CallInfo",
    "TelephonyAdapter",
]
