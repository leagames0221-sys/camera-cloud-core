"""Small helpers shared by the unit tests."""
import uuid

from app.domain import video_path


def cam_url(tenant: uuid.UUID, camera: uuid.UUID, tail: str = "") -> str:
    return f"/api/v1/tenants/{tenant}/cameras/{camera}{tail}"


def path_of(tenant: uuid.UUID, camera: uuid.UUID) -> str:
    return video_path(tenant, camera)
