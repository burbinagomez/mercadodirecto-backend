"""Pydantic schemas for auth + profiles."""
from pydantic import BaseModel, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    role: str  # "farmer" | "consumer" | "restaurant"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class FarmerProfileIn(BaseModel):
    farm_name: str
    department: str
    city: str
    bio: str = ""
    produces: str = ""


class ConsumerProfileIn(BaseModel):
    full_name: str = ""
    address: str = ""
