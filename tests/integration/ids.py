"""Fixed ids of the demo data, repeated here on purpose: the integration tests only talk to the outside doors of the
running stack and never import the application. A unit test keeps this file equal to the application's seed data."""
TENANT_A = "20000000-0000-4000-8000-00000000000a"
TENANT_B = "20000000-0000-4000-8000-00000000000b"
CAM_A1 = "30000000-0000-4000-8000-0000000000a1"
CAM_A2 = "30000000-0000-4000-8000-0000000000a2"
CAM_B1 = "30000000-0000-4000-8000-0000000000b1"

USER_SUB = {
    "partner-admin": "40000000-0000-4000-8000-0000000000f0",
    "a-admin": "40000000-0000-4000-8000-0000000000a1",
    "a-viewer": "40000000-0000-4000-8000-0000000000a2",
    "b-admin": "40000000-0000-4000-8000-0000000000b1",
    "b-viewer": "40000000-0000-4000-8000-0000000000b2",
}
