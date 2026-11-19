# Media Source Optimizer — lokale ontwikkel- en teststack

> **P-INSTALLATIE staat open.** De installatie op TrueNAS niet uitvoeren tot de poort is gehaald.
> Het formulier voor de standaard Custom App is beschreven in [TRUENAS_CUSTOM_APP.md](TRUENAS_CUSTOM_APP.md).
> De hardwareproef na vrijgave staat in [HARDWARE_ACCEPTANCE.md](HARDWARE_ACCEPTANCE.md).

De stack in `deploy/truenas/compose.yaml` dient voor ontwikkeling, CI en end-to-end-tests op een lokale
Docker-host. De proef in `tests/stack/` gebruikt testgegevens en simulatiediensten.

## Wat er draait

| Container | Rol | Netwerk | Draait als |
|---|---|---|---|
| `init` | eenmalig per start: mappen + eigenaar 568:568 + databasewachtwoord genereren | geen | root met alleen `CHOWN`, `DAC_OVERRIDE`, `FOWNER` |
| `postgres` | PostgreSQL 16.15 (gepind op tag + digest) | `db` (intern, geen uitgaand verkeer) | 568:568, géén capabilities |
| `migrate` | eenmalig: databaseschema bijwerken | `db` | 568:568 |
| `api` | core-API, Newznab/Torznab-feeds (niet gepubliceerd) | `db`, `app` | 568:568 |
| `worker` | achtergrondtaken (back-ups, polling, herstel) | `db`, `app` | 568:568 |
| `web` | gebruikersinterface + same-origin proxy (`/api/v1`, `/newznab`, `/torznab` → `api`) | `app`, poort **38081** | 568:568 |

Alle containers: `no-new-privileges`, `cap_drop: ALL`, read-only root-filesystem met tmpfs voor `/tmp`, vaste
CPU-/geheugen-/PID-limieten, logrotatie (20 MB × 5). Geen Docker-socket, geen host-netwerk, geen privileged
containers. Alleen `web` publiceert een poort.

**Geheimen.** In de YAML staan géén API-sleutels of wachtwoorden. Het databasewachtwoord genereert `init`
(`APPDATA/db/password`, 64 hex-tekens, modus 0400). API-sleutels en wachtwoorden van Prowlarr, SABnzbd,
qBittorrent, MediaGuard en providers voer je in de setupwizard in; MSO bewaart ze AES-256-GCM-versleuteld in
de database met de sleutel `APPDATA/secrets/vault.key` (wordt bij de eerste start aangemaakt, modus 0600).

## Lokale proef

Draai vanaf de hoofdmap van de repo `tests/stack/run.sh`. De proef bouwt de losse containers, start de
simulatiediensten en schrijft de resultaten onder `tests/stack/results/`. De gemeten limieten en het
beheer van de lokale stack staan in [OPERATIONS.md](OPERATIONS.md).

Voor dagelijks gebruik van MSO, inclusief de setupwizard, koppelingen en back-ups, zie
[USER_GUIDE.md](USER_GUIDE.md).
