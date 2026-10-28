"""Fixed ids of the demo data. The realm import file uses the same user ids; a unit test keeps them equal."""
import uuid

PARTNER_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PARTNER_NAME = "デモ パートナー"

TENANT_A = uuid.UUID("20000000-0000-4000-8000-00000000000a")
TENANT_B = uuid.UUID("20000000-0000-4000-8000-00000000000b")
TENANTS = [(TENANT_A, "A 社"), (TENANT_B, "B 社")]

CAM_A1 = uuid.UUID("30000000-0000-4000-8000-0000000000a1")
CAM_A2 = uuid.UUID("30000000-0000-4000-8000-0000000000a2")
CAM_B1 = uuid.UUID("30000000-0000-4000-8000-0000000000b1")
CAMERAS = [
    (CAM_A1, TENANT_A, "A 社 カメラ 1"),
    (CAM_A2, TENANT_A, "A 社 カメラ 2"),
    (CAM_B1, TENANT_B, "B 社 カメラ 1"),
]

USER_PARTNER_ADMIN = uuid.UUID("40000000-0000-4000-8000-0000000000f0")
USER_A_ADMIN = uuid.UUID("40000000-0000-4000-8000-0000000000a1")
USER_A_VIEWER = uuid.UUID("40000000-0000-4000-8000-0000000000a2")
USER_B_ADMIN = uuid.UUID("40000000-0000-4000-8000-0000000000b1")
USER_B_VIEWER = uuid.UUID("40000000-0000-4000-8000-0000000000b2")

# (user id, role, partner id or None, tenant id or None)
MEMBERSHIPS = [
    (USER_PARTNER_ADMIN, "partner_admin", PARTNER_ID, None),
    (USER_A_ADMIN, "tenant_admin", None, TENANT_A),
    (USER_A_VIEWER, "viewer", None, TENANT_A),
    (USER_B_ADMIN, "tenant_admin", None, TENANT_B),
    (USER_B_VIEWER, "viewer", None, TENANT_B),
]

# ---- Company C (fictional): served by stamp 2, the copy of the video, state and index parts that holds the later
# demo data. Kept apart from the lists above on purpose: companies A and B and their three cameras are what the earlier
# integration tests look at, and they are not changed.
STAMP_C = 2
TENANT_C = uuid.UUID("20000000-0000-4000-8000-00000000000c")
EXTRA_TENANTS = [(TENANT_C, "C 社", STAMP_C)]            # (id, name, stamp id)
CAM_C1 = uuid.UUID("30000000-0000-4000-8000-0000000000c1")
EXTRA_CAMERAS = [(CAM_C1, TENANT_C, "C 社 カメラ 1")]
USER_C_ADMIN = uuid.UUID("40000000-0000-4000-8000-0000000000c1")
USER_C_VIEWER = uuid.UUID("40000000-0000-4000-8000-0000000000c2")
EXTRA_MEMBERSHIPS = [
    (USER_C_ADMIN, "tenant_admin", None, TENANT_C),
    (USER_C_VIEWER, "viewer", None, TENANT_C),
]
