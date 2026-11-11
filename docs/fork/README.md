# Fork-Erweiterungen

Dieser Fork folgt `upstream/dev` und ergänzt einige Funktionen. Hier steht, was sie tun und wo sie im Code sitzen. Die Fork-Tests liegen in eigenen Dateien unter `tests/`.

## Durchreich-Modus (Passthrough)

Für Assistenten, die ihren Kontext selbst verwalten. Chats mit einem Modell, das auf ein Muster aus dem Setting `passthrough_model_patterns` passt (Glob, Standard `absoluter-agent*`), gehen unverändert an das Modell:

- nur die User- und Assistant-Züge: kein Preface (Gedächtnis, RAG, Websuche, Skills, Notizen), keine Datumszeile, keine Kompaktierung, kein Agent-Modus, kein Research, kein Fitness-Coach-Prompt
- keine Hintergrundaufgaben (Titel, Gedächtnis, Zusammenfassungen) auf diesem Modell
- jede Anfrage trägt die Chat-ID im Header `X-Odysseus-Chat`
- `/compact` lehnt solche Chats ab, „Umschreiben“ nutzt das Hintergrund-Modell
- Anhänge werden nicht verarbeitet. Je Anhang kommt eine Zeile an den Text:
  `[Anhang: <name> | id=<upload-id> | mime=<mime> | bytes=<größe>]`
  Der Name ist der gesäuberte Upload-Name. Das Modell holt die Datei selbst über `GET /api/upload/{id}` (Besitzer geprüft).

Code: `src/passthrough.py` (`is_passthrough_model` ist die einzige Entscheidungsstelle, gespiegelt in `static/js/passthrough.js`), Aufrufe in `routes/chat_routes.py`, `routes/chat_helpers.py`, `src/chat_handler.py`, `src/context_compactor.py`, `src/task_endpoint.py`, `routes/session_routes.py`, `routes/history/history_routes.py`. Tests: `tests/test_passthrough_*.py`.

Wer neue Kontext-Einspeisungen, Eskalationen oder Modell-Fallbacks einbaut, prüft sie gegen `is_passthrough_model`.

## API-Tokens für Companion-Clients

- Kalender, Notizen, E-Mail und Tasks akzeptieren Bearer-Tokens und arbeiten im Namen des Token-Besitzers (`require_user_api_aware` in `src/auth_helpers.py`).
- Token-Scope `agent:privileged`: hebt für diesen Token die Agent-Tool-Grenze und die Sperre beim Fortsetzen nach Tool-Freigaben auf (`is_delegated_agent_credential`). Gesetzt wird er pro Token im Admin-Panel.

## Chat-Widgets

Modelle können interaktive Widgets als ```` ```widget ````-Fence ausgeben. Gerendert wird in einer Sandbox-Frame-Seite mit eigener CSP (`static/widget-frame.html`, `WIDGET_FRAME_CSP` in `core/middleware.py`), nicht per srcdoc. Der Prompt wird nur eingespeist, wenn der Client `widgets=true` sendet. Die Basis-Styles in `static/js/widgetDocument.js` bilden auch die `--ody-*`-Variablen des Companion-Formats ab. Code: `static/js/widgets.js`, `static/js/widgetDocument.js`, `src/widget_prompt.py`, `static/css/widgets.css`.

## Media Studio: lokale Modelle

Lokale Bild- und Video-Endpunkte (ModelEndpoint `model_type` „image“) erscheinen als `local/<endpoint_id>/<model>`. Die Fähigkeiten kommen von `GET /models` bzw. `/videos/models` des Endpunkts. Lokale Fotos laufen als Hintergrund-Jobs (`job_status` „pending“, Abfrage über `/api/studio/jobs/{id}`), Videos über `<base>/videos`. Code: `routes/studio/studio_routes.py`, `routes/studio/studio_helpers.py`.

## Fitness-Coach

Daten unter `data/users/<user>/fitness_data` (`FITNESS_DATA_DIR`). Der Agent darf dieses Verzeichnis lesen. Code: `services/fitness/`.

## Verträge, die Upstream-Tests prüfen

Neue Fork-Dateien müssen diese Regeln einhalten, sonst schlagen Upstream-Tests fehl:

- Stylesheets in `index.html` tragen den gemeinsamen `?v=`-Token und stehen im Precache von `static/sw.js`. Ein Modul wird überall mit demselben `?v=`-Specifier importiert.
- `CACHE_NAME` folgt dem Muster `odysseus-v<N>-pr40-editor-precache`.
- Settings werden über `postSettings` in `static/js/settings/api.js` geschrieben.
- Jede gelesene `ODYSSEUS_*`-Variable hat einen Eintrag in `scripts/generate_env_reference.py`. Danach `website/configuration-reference.md` neu erzeugen.
- Upload-Grenzen werden in allen drei Compose-Dateien weitergereicht.

## Deploy-Hinweise

Update-Anleitung für eigene Instanzen: [upgrade-2026-11.md](upgrade-2026-11.md).


- `docker-compose.yml` nennt standardmäßig das Upstream-Image. Für Fork-Builds `ODYSSEUS_IMAGE` auf einen lokalen Namen setzen und mit `--build` starten.
- Der Upstream-Standardport ist 7011 (`APP_PORT`).
