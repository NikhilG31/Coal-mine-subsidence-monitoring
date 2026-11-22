"""Pydantic schemas for mine sites, tunnels, and hardware gateway provisioning.

Owns input validation rules for mine creation, tunnel allocation, and gateway registration.
Must NOT execute database transactions, verify passwords, or handle HTTP routing directly.
"""
from typing import List, Optional
from pydantic import BaseModel, field_validator, model_validator

from app.services.packet_codec import norm_mac


class CreateMineRequest(BaseModel):
    name: str
    subsidiary: Optional[str] = None
    status: str = "active"
    method: str = "underground"
    area_ha: Optional[float] = None
    max_depth_m: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not (1 <= len(clean) <= 255):
            raise ValueError("Mine name must be between 1 and 255 characters")
        return clean

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = {"active", "maintenance", "suspended", "closed"}
        if v not in valid:
            raise ValueError(f"Invalid status '{v}'. Permitted: {sorted(valid)}")
        return v

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        valid = {"opencast", "underground", "mixed"}
        if v not in valid:
            raise ValueError(f"Invalid method '{v}'. Permitted: {sorted(valid)}")
        return v

    @field_validator("area_ha")
    @classmethod
    def validate_area(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("area_ha must be non-negative")
        return v

    @field_validator("max_depth_m")
    @classmethod
    def validate_depth(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("max_depth_m must be non-negative")
        return v

    @model_validator(mode="after")
    def validate_coordinates(self) -> "CreateMineRequest":
        lat = self.lat
        lon = self.lon
        if (lat is None and lon is not None) or (lat is not None and lon is None):
            raise ValueError("Latitude and longitude must both be provided or both omitted")
        if lat is not None and not (-90.0 <= lat <= 90.0):
            raise ValueError("Latitude must be between -90 and 90")
        if lon is not None and not (-180.0 <= lon <= 180.0):
            raise ValueError("Longitude must be between -180 and 180")
        return self


class CreateTunnelRequest(BaseModel):
    name: str
    code: str
    depth_m: Optional[float] = None
    status: str = "active"

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not (1 <= len(clean) <= 255):
            raise ValueError("Tunnel name must be between 1 and 255 characters")
        return clean

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        clean = v.strip().upper()
        import re
        if not re.match(r"^[A-Z0-9_-]{1,64}$", clean):
            raise ValueError("Tunnel code must match ^[A-Za-z0-9_-]{1,64}$")
        return clean

    @field_validator("depth_m")
    @classmethod
    def validate_depth(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("depth_m must be non-negative")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        valid = {"active", "maintenance", "closed"}
        if v not in valid:
            raise ValueError(f"Invalid status '{v}'. Permitted: {sorted(valid)}")
        return v


class CreateGatewayRequest(BaseModel):
    name: str
    mac: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        clean = v.strip()
        if not (1 <= len(clean) <= 255):
            raise ValueError("Gateway name must be between 1 and 255 characters")
        return clean

    @field_validator("mac")
    @classmethod
    def validate_mac(cls, v: str) -> str:
        normalized = norm_mac(v)
        if normalized == "UNKNOWN":
            raise ValueError("Invalid MAC address")
        return normalized
