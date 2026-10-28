# 0002. How tenants and roles are represented

## Status
Accepted (2026-10-24).

## Context
The service has three kinds of users: a partner administrator (looks after several customer companies), a tenant administrator (one customer company) and a viewer (one customer company). Customer companies must never see each other's cameras or video. The login service (Keycloak) can model groups of users in three ways: one realm per tenant, one realm with groups, or Organizations ([Keycloak server administration guide](https://www.keycloak.org/docs/latest/server_admin/index.html): "Realms are isolated from one another"; Organizations are a built-in feature).

Whatever is chosen, the application still has to know, for every request, which tenant and which cameras a user may touch, and which video paths a token may open. The Azure architecture guidance on multitenant solutions ([overview of approaches](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/approaches/overview)) describes the range between sharing everything and separating everything, and says that each approach between them trades off scale, isolation, cost efficiency, performance, complexity and manageability.

## Decision
- **Keycloak only answers "who is this person".** There is one realm (`ccc`) for the whole service. Keycloak's administration guide puts the tenants of a business inside one realm with its Organizations feature ("It enables multi-tenancy within a realm"); this project keeps the tenants in its own database instead, and that feature is not used. It holds the five demo users and one public client (now nine demo users and a second public client, `ccc-device`, 0009). It holds no groups, roles, organizations or identity providers. A user's Keycloak `id` is the `sub` claim of the token and is fixed in the realm import file, so the application's rows can name the user before the user has ever logged in.
- **The application owns the rest** in PostgreSQL: partners, tenants, memberships (user id, role, partner or tenant), cameras, status and the recording index. A membership is either "partner administrator of a partner" or "tenant administrator / viewer of a tenant".
- **One data layer, with the tenant id as a required argument** (`store.py`). Every method that reads or writes tenant data takes `tenant_id`; there is no call that lists cameras, segments or statuses across tenants. The few methods that cannot know the tenant first (`internal_*`: the MQTT intake and the segment hook look a camera up by its id) are named so and are used only by the state intake, the closed-segment door, the camera's token request and the reconciliation.
- **Operation x role is a table in code** (`rules.py`, copied in `docs/requirements.md`) and is checked on the server for every request. A partner administrator can create and list tenants, list cameras and add users, but never watches video.
- **Another company's things are `404`**, with the same body as things that do not exist. A role that is too low inside one's own tenant is `403`.
- **PostgreSQL Row Level Security is not used in this version.** It would give a second line of defence, but superusers and roles with `BYPASSRLS` always bypass it, and table owners bypass it unless `FORCE ROW LEVEL SECURITY` is set ([PostgreSQL 17 documentation, "Row Security Policies"](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)). The application connects as `ccc`, the user that the `postgres` image creates from `POSTGRES_USER` as a superuser (the image's documentation), so row security would not apply to it even with `FORCE`. Keeping the rule in one place and testing it hard was preferred to having it in two places. Comparing the two is left for a later version.

## Consequences
- Tenants, roles and cameras are created by API calls and rows, not by Keycloak administration. A new user needs a Keycloak account and a membership row (`POST /api/v1/tenants/{id}/members`); there is no screen for either yet.
- The isolation rests on one rule file and one data layer, so the unit tests (`tests/unit/test_authorization.py`) try every role against every operation and the mutation check (`mutation/patches/m1_tenant_check.patch`) breaks the rule on purpose to see them fail.
- A bug in the data layer would expose other tenants' rows; RLS would not catch it. This is accepted for now and written down here.
- `404` for other companies' things hides which ids exist, at the price that a client cannot tell "not yours" from "gone".
- The partner/tenant structure is ours, so it is not portable to another login service without moving the memberships table, but nothing else.

## Alternatives not chosen
- **One realm per tenant**, **one realm with groups**, **Organizations.** Each would put tenant membership into Keycloak, while the application must also know it (for per-tenant video paths and for the data layer). Two places to keep equal, and a Keycloak-specific shape of the token, for no gain at this size.

## Update (2026-10-25)
The later version kept the tenant layer in one place (`RoutedStore`, which also turns a tenant into a stamp) and separated tenants
physically by stamp first (0006). Row Level Security is still not used; its comparison with this layer is listed there under "Not built".
