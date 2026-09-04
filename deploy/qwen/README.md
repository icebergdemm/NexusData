# Qwen/KoboldCpp для AI-досье

На тестовом хосте `nexusdataasper` модель уже лежит в
`/opt/models/qwen25/qwen25-3b-q4_k_m.gguf`, бинарник KoboldCpp — рядом.
Шаблон сервиса рассчитан на KoboldCpp 1.117, один параллельный запрос,
контекст 8192 и максимум 700 выходных токенов.

Перед запуском:

1. Создать пользователя `koboldcpp` и каталог `/etc/koboldcpp`.
2. Положить в `/etc/koboldcpp/player-dossier.env` строку
   `KOBOLD_PASSWORD=<случайный секрет>` с правами `0600`.
3. Положить TLS certificate/key в `/etc/koboldcpp/player-dossier.crt` и
   `/etc/koboldcpp/player-dossier.key` (сертификат `0640`, ключ `0640`, владелец
   `root:koboldcpp`). Публичная копия сертификата для NexusData лежит рядом
   с этим README: `deploy/qwen/koboldcpp-player-dossier.crt`. Её можно указать
   в AI-центре как `deploy/qwen/koboldcpp-player-dossier.crt` или загрузить
   через форму CA. Приватный ключ в NexusData не нужен; отключать проверку TLS
   нельзя.
4. Скопировать unit в `/etc/systemd/system/`, выполнить
   `systemctl daemon-reload && systemctl enable --now koboldcpp-player-dossier`.
5. В UFW открыть `8000/tcp` только для `144.31.195.171`; временный доступ
   `92.38.222.172` удалить после ручного smoke-теста.

Проверка API идёт на
`https://213.108.170.48:8000/v1/chat/completions` с заголовком
`Authorization: Bearer <KOBOLD_PASSWORD>`. В production пароль не должен
появляться в Git, shell history или логах systemd.
