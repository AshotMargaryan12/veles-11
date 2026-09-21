# Установка на VPS

```bash
# 1. Пользователь и каталоги
sudo useradd -r -m -d /opt/tghunter -s /bin/bash tghunter
sudo mkdir -p /var/log/tghunter && sudo chown tghunter:tghunter /var/log/tghunter

# 2. Код и зависимости
sudo -u tghunter git clone <repo> /opt/tghunter
cd /opt/tghunter
sudo -u tghunter python3.11 -m venv .venv
sudo -u tghunter .venv/bin/pip install -r requirements.txt

# 3. Конфигурация
sudo -u tghunter cp .env.example .env
sudo -u tghunter nano .env          # TG_API_ID, TG_API_HASH и остальное
sudo chmod 600 .env

# 4. Сессия Telegram — интерактивно, один раз
sudo -u tghunter .venv/bin/python -m tghunter login

# 5. Проверка вручную до постановки в расписание
sudo -u tghunter .venv/bin/python -m tghunter run --stream crypto_core

# 6. Расписание
sudo cp deploy/tghunter.service deploy/tghunter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tghunter.timer
systemctl list-timers tghunter.timer
```

Файл сессии `sessions/hunter.session` — секрет уровня пароля от аккаунта.
Он в `.gitignore`; на сервере держите права `600` и не копируйте между машинами.
