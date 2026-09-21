# Установка ИИ-анализатора диалогов (crmai)

Этап Э5 из ТЗ: развёртывание на VPS, логи, ротация токенов amoCRM.

Два способа: systemd (проще отлаживать) или docker-compose (проще переносить).
Оба поднимают один процесс: вебхук Wazzup + расписание батчей внутри него.

---

## 1. Подготовка

```bash
sudo useradd -r -m -d /opt/crmai -s /usr/sbin/nologin crmai
sudo mkdir -p /opt/crmai /var/log/crmai
sudo chown -R crmai:crmai /opt/crmai /var/log/crmai
```

Выложить репозиторий в `/opt/crmai` и заполнить `.env` (список переменных —
в `.env.example`, раздел «ИИ-анализ диалогов»).

```bash
sudo -u crmai git clone <repo> /opt/crmai
cd /opt/crmai
sudo -u crmai cp .env.example .env && sudo -u crmai nano .env
sudo chmod 600 .env
```

Проверка конфигурации до запуска:

```bash
sudo -u crmai .venv/bin/python -m crmai config-check
```

## 2. Вариант A: systemd

```bash
sudo -u crmai python3.11 -m venv /opt/crmai/.venv
sudo -u crmai /opt/crmai/.venv/bin/pip install -r /opt/crmai/requirements-crmai.txt

sudo cp /opt/crmai/deploy/crmai.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now crmai
sudo systemctl status crmai
```

Логи: `/var/log/crmai/service.log` и `journalctl -u crmai -f`.

Ротация логов — положить `/etc/logrotate.d/crmai`:

```
/var/log/crmai/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

## 3. Вариант B: docker-compose

```bash
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f
```

База и токены лежат в томе `crmai-data`, каталог `prompts/` смонтирован
только на чтение: правки `company_context.md` подхватываются следующим батчем
без пересборки.

## 4. HTTPS и подписка Wazzup

Wazzup ходит только на публичный HTTPS-адрес, поэтому перед сервисом нужен
nginx (или Caddy) с сертификатом:

```nginx
location /webhook/wazzup {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    # вебхук должен ответить за 2 секунды, обработка идёт фоном
    proxy_read_timeout 10s;
}
```

Дальше в личном кабинете Wazzup указать URL
`https://<домен>/webhook/wazzup` и подписаться на события типа `messages`.
Если задан `WAZZUP_WEBHOOK_SECRET`, добавить его в URL: `?secret=...`.

Проверка живости: `curl https://<домен>/health`.

## 5. Ротация токенов amoCRM

Токен интеграции живёт сутки, refresh — три месяца, и **refresh одноразовый**:
после каждого обновления приходит новая пара. Поэтому:

* сервис обновляет токен сам при первом ответе 401 и сразу пишет новую пару в
  `AMO_TOKEN_FILE` (по умолчанию `amo_tokens.json`, в `.gitignore`);
* при старте файл читается и перекрывает значения из `.env` — значит, после
  рестарта интеграция продолжает работать;
* в `.env` держать первую пару токенов из карточки интеграции; менять её руками
  нужно только если файл потерян или интеграция пересоздана.

Если сервис молчал дольше трёх месяцев, refresh протухнет. Тогда: в amoCRM
открыть «Настройки — Интеграции — <ваша интеграция>», выпустить новый ключ,
положить пару в `.env`, удалить `amo_tokens.json` и перезапустить сервис.

Проверить доступ:

```bash
sudo -u crmai /opt/crmai/.venv/bin/python -m crmai amo-check
# и записать тестовое примечание в конкретную сделку (этап Э2):
sudo -u crmai /opt/crmai/.venv/bin/python -m crmai amo-check --lead-id 12345
```

## 6. Ручной запуск батчей

Расписание работает внутри `serve`, но батч можно запустить руками —
например, при разборе инцидента или на приёмке:

```bash
python -m crmai run-status              # уровень 1
python -m crmai run-quality --date 2026-09-21   # уровень 2
python -m crmai digest --date 2026-09-21 --send # дайджест
```

Если предпочитаете cron вместо встроенного планировщика, запускайте сервис как
`python -m crmai serve --no-scheduler` и добавьте в crontab пользователя:

```
0  19 * * 1-5 cd /opt/crmai && .venv/bin/python -m crmai run-status
30 19 * * 1-5 cd /opt/crmai && .venv/bin/python -m crmai run-quality
45 19 * * 1-5 cd /opt/crmai && .venv/bin/python -m crmai digest --send
```

## 7. Резервные копии

Ценного состояния немного: SQLite-база и файл токенов.

```bash
sqlite3 /opt/crmai/crm_analyzer.db ".backup '/var/backups/crm_$(date +%F).db'"
```
