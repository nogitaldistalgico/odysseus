# Update auf den Stand vom November 2026 (für Betreiber einer eigenen Instanz)

Dieser Stand enthält einen großen Upstream-Merge. Bei einer Docker-Compose-Installation des Forks sind vier Dinge anders als vorher, und alle vier scheitern still, wenn man sie übersieht. Die Schritte unten setzen voraus, dass Odysseus mit `docker compose` aus einem Checkout dieses Repos läuft. Alle Befehle laufen im Ordner mit der `docker-compose.yml`.

## Was sich ändert

1. **Port:** Der Standard von `APP_PORT` ist jetzt **7011** statt 7000. Wer den alten Port behalten will (weil Clients ihn kennen), setzt ihn in der `.env`.
2. **Image:** `docker-compose.yml` nennt jetzt `image: ${ODYSSEUS_IMAGE:-ghcr.io/odysseus-dev/odysseus:latest}`. Ohne eigenen Namen und ohne `--build` läuft das Upstream-Image, und die Fork-Funktionen fehlen.
3. **Chroma:** Das Image ist auf 1.5.9 gepinnt, das Volume hängt jetzt unter `/data` statt `/chroma/chroma`. Chroma ab Version 1.0 schreibt nach `/data`. Lief bisher `chroma:latest` mit dem alten Mount, liegen die Daten nur in der Container-Schicht und gehen beim Neuerstellen verloren, wenn man sie nicht vorher herauskopiert.
4. **Platz:** Das neue Odysseus-Image enthält Chromium und ist über 4 GB groß. Während des Builds liegen altes und neues Image nebeneinander.

Weitere Neuerungen:
- Eigene MCP-Tools gelten als unbekannt und brauchen öfter eine Freigabe. Lokale stdio-Tools lassen sich mit `ODYSSEUS_LOCAL_MCP_READ_TOOLS` / `ODYSSEUS_LOCAL_MCP_WRITE_TOOLS` deklarieren (Format `mcp__<server>__<tool>`, kommagetrennt). HTTP-/SSE-Server sind nicht deklarierbar.
- Chats mit Modellen, deren Name auf `absoluter-agent*` passt, laufen im Durchreich-Modus (siehe `README.md`). Ist das bei dir ungewollt, das Setting `passthrough_model_patterns` leeren.
- `ODYSSEUS_CHAT_UPLOAD_MAX_BYTES` (Standard 10 MiB) begrenzt Chat-Anhänge.

## Ablauf

**1. Vorher prüfen** (der Dienst läuft weiter):

```sh
df -h /
docker system df
git status -sb && git log --oneline -1
docker compose exec chromadb sh -c 'du -sh /data /chroma/chroma' 2>&1
```

- Frei sein sollten mindestens 6 GB. Sonst vorher Platz schaffen oder die Platte vergrößern.
- Den aktuellen Commit notieren, das ist der Rollback-Stand.
- Zeigt `/data` mehr als ein paar KB und `/chroma/chroma` fast nichts, liegen die Chroma-Daten in der Container-Schicht. Dann ist Schritt 5 Pflicht.

**2. Sichern:**

```sh
cp .env .env.vor-update
docker cp $(docker compose ps -q chromadb):/data ./chroma-sicherung
```

**3. `.env` anpassen:**

```sh
APP_PORT=7000                       # nur wenn der bisherige Port bleiben soll
ODYSSEUS_IMAGE=odysseus-fork:local  # beliebiger lokaler Name
```

`APP_BIND` bleibt wie bisher (Standard `127.0.0.1`).

**4. Code holen, Konfiguration prüfen, bauen** (der alte Dienst läuft weiter):

```sh
git pull -q --ff-only
docker compose config | grep -nE 'image:|host_ip|published|target: /data'
docker compose --progress plain build
docker compose pull chromadb searxng ntfy
```

In der Ausgabe von `config` müssen der eigene Image-Name, der gewünschte Port und `target: /data` beim Chroma-Volume stehen. Der Build dauert lange, am besten in `screen` oder `tmux` starten.

**5. Umschalten** (ab hier ist Odysseus kurz aus):

```sh
docker compose stop
docker cp $(docker compose ps -aq chromadb):/data ./chroma-umschalt
docker run --rm -v <projekt>_chromadb-data:/data -v "$PWD/chroma-umschalt":/src:ro alpine sh -c 'cp -a /src/. /data/ && ls -la /data'
docker compose up -d
```

`<projekt>` ist der Name des Compose-Projekts (`docker volume ls | grep chromadb`). Die Kopie aus dem gestoppten Container ist konsistenter als die Sicherung aus Schritt 2.

**6. Prüfen:**

```sh
docker compose ps
docker compose logs --tail=80 odysseus | grep -E "startup complete|MemoryVectorStore ready|ERROR"
```

Erwartet: alle Container „Up“, `Application startup complete` und `MemoryVectorStore ready … entries=<n>` mit der bisherigen Zahl an Erinnerungen. Danach die Web-UI hart neu laden, weil ein neuer Service-Worker-Cache aktiv wird. Meldungen `pthread_setaffinity_np failed` in Containern ohne CPU-Pinning sind harmlos.

**7. Aufräumen**, wenn alles läuft:

```sh
docker image prune -a -f
docker builder prune -f
```

Das löscht alle Images, die kein Container mehr nutzt, also auch das alte Odysseus-Image. Danach gibt es kein Rollback-Image mehr.

## Rollback

`.env.vor-update` zurückkopieren, `git checkout <alter-commit>`, `docker compose up -d --build`. Für Chroma den Inhalt von `chroma-sicherung` zurückspielen, falls nötig.
