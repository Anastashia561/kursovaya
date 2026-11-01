# Контролируемый шлюз исходящего трафика при УБИ.193

[![Verify microservices](https://github.com/Anastashia561/kursovaya/actions/workflows/verify.yml/badge.svg)](https://github.com/Anastashia561/kursovaya/actions/workflows/verify.yml)
[![Kubernetes integration](https://github.com/Anastashia561/kursovaya/actions/workflows/cluster.yml/badge.svg)](https://github.com/Anastashia561/kursovaya/actions/workflows/cluster.yml)

Микросервисный прототип контроля исходящих запросов. Шлюз NGINX запрашивает решение выделенного сервиса безопасности и передает разрешенный запрос по HTTPS. Правила ограничивают адрес назначения, суммарный объем и число запросов за интервал, заявленный тип данных и временно заблокированные источники. Решения сохраняются в журнале и экспортируются в Prometheus.

## Компоненты

| Компонент | Порт | Реализация |
|---|---:|---|
| app-service | 8000 | Защищаемое приложение, синтетические записи PostgreSQL, исходящие POST через шлюз |
| gateway-service | 8080 | NGINX, auth_request, проверка формы запроса, HTTPS upstream с проверкой сертификата |
| security-service | 8081 | R1–R5, блокировка строки PostgreSQL, общий бюджет двух реплик, outbox, метрики |
| auth-service | 8082 | bcrypt, JWT RS256, роли admin/auditor, административное API правил |
| log-service | 8083 | Идемпотентная запись событий по UUID, чтение с JWT |
| Prometheus / Grafana | 9090 / 3000 | Сбор обеих реплик, правило оповещения и подготовленный дашборд |
| external-service | 8443, Service 443 | Изолированный HTTPS-стенд внешнего сервиса; только синтетические данные |

`common/` содержит модели и проверку JWT; `services/` — исходные функции; `deploy/` — конфигурации; `scripts/` — подготовку и запуск; `tests/` — проверки. PostgreSQL 16 используется для правил, счетчиков, outbox, журнала, учетных записей и отдельно для данных приложения. Образ Python 3.12 общий; каждый сервис запускается отдельным Deployment и процессом.

## Локальный профиль Windows

Требуется Python 3.12. `START_LOCAL.cmd` создает `.venv`, устанавливает зависимости и запускает сервисы. NGINX 1.28.0 для Windows загружается с nginx.org в исключенный из Git каталог `.local`. Сервисы слушают только 127.0.0.1. В локальном профиле идентификация источника явно ограничена loopback, а PostgreSQL заменен SQLite. Этот профиль проверяет HTTP, настоящий NGINX и TLS; он не подтверждает блокировки строк PostgreSQL или NetworkPolicy.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m scripts.run_local --smoke
```

Без `--smoke` процессы работают до Ctrl+C. Локальные адреса: приложение `http://127.0.0.1:18000/docs`, авторизация `http://127.0.0.1:18082/docs`, журнал `http://127.0.0.1:18083/docs`, метрики `http://127.0.0.1:18081/metrics`. Случайные пароли admin/auditor сохраняются в `.local/local/credentials.json`. Скрипт проверки сам читает их; в Git они не передаются. Повторный запуск сохраняет локальные данные.

## Kubernetes

Требуются Docker, kubectl, Minikube и Python 3.12 с зависимостями проекта. Ориентир для стенда: 2 CPU, 4 ГБ памяти для Minikube и свободное место для образов. Это параметры окружения, а не результат измерения производительности.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
minikube start -p ubi193 --driver=docker --cni=calico --cpus=2 --memory=4096 --kubernetes-version=v1.33.1
python -m scripts.deploy --profile ubi193
python -m scripts.smoke --cluster
```

Пространства имен `app`, `security` и `monitoring` содержат приложение, защиту и наблюдение. `external` содержит только тестовый внешний HTTPS-сервис. Скрипт создает случайные Secret и пару JWT, отдельный центр сертификации и сертификат внешнего сервиса, собирает образ, применяет NetworkPolicy, ожидает БД, выполняет bootstrap Job и проверяет готовность Deployment. `.local/cluster` необходимо сохранять при повторном запуске с теми же PVC: пароли должны соответствовать данным томов. В Git включены несекретные YAML и генератор; актуальные IP Kubernetes API добавляются отдельной политикой при развертывании.

Шлюз и сервис безопасности имеют по две реплики. Счетчики и временная блокировка изменяются под `SELECT FOR UPDATE` одной строки сервиса. Запись outbox выполняется в той же транзакции, что и резервирование бюджета. Недоступность log-service оставляет событие в outbox; повторная доставка безопасна благодаря UUID. Для правил, пользователей и журнала созданы отдельные роли PostgreSQL. Приложение не получает соединение с security-db.

Политики по умолчанию запрещают ingress и egress. Отдельно разрешены DNS по TCP/UDP 53 и нужные пары сервисов. app-service имеет выход только к gateway-service:8080 и app-db:5432; прямое подключение к внешнему HTTPS-сервису запрещено. Calico должен реально применять NetworkPolicy. Проверка `scripts.smoke --cluster` отдельно проверяет попытку обхода, а также доступность обеих целей Prometheus.

## API и контракт шлюза

* `POST app-service/api/v1/send`: `{"data":{"message":"synthetic hello"}}`; необязательные `host` и `media` позволяют проверить отказ R2/R5.
* `POST auth-service/api/v1/token`: JSON `username`, `password`; результат — Bearer JWT на 900 секунд.
* `PUT auth-service/api/v1/rules/app-service`: JWT admin и объект `hosts`, `types`, `max_bytes`, `max_requests`, `window_seconds`, `block_seconds`.
* `GET log-service/api/v1/events`: JWT admin или auditor, необязательный `limit` до 200.
* `GET security-service/metrics`: счетчики решений, отказов, зарезервированных байтов и гистограмма времени проверки.

В Kubernetes внутренние API доступны через `kubectl port-forward`, например `kubectl -n app port-forward service/app-service 18000:8000`. Аналогично: auth-service 18082:8082, log-service 18083:8083, Prometheus 19090:9090, Grafana 13000:3000. Административные порты не публикуются как NodePort. Grafana использует admin и случайный `grafana_password` из локального комплекта кластерных секретов.

Маршрут шлюза: POST `/ext/external-service.external.svc.cluster.local/echo`. Передаются только явно заданные Host, Content-Type и Content-Length; пользовательские заголовки и Authorization внешнему сервису не пересылаются. Query string, другие методы, chunked и запросы без числового Content-Length отвергаются. NGINX проверяет HTTP framing и буферизует тело перед отправкой. Бюджет резервируется по заявленной длине до передачи; неполный запрос может консервативно израсходовать квоту. Фиксированная таблица upstream и сетевые правила ограничивают маршрутизацию независимо от API правил. Для произвольных внешних API контракт надо расширять согласованно, вместе с тестами.

## Проверки и границы

`Verify microservices` запускает pytest с PostgreSQL, включая параллельное расходование общего бюджета, проверяет актуальность YAML и собирает Docker-образ. `Kubernetes integration` поднимает временный Minikube с Calico, развертывает полный состав, проверяет ALLOW, R1–R5, роли, TLS, журнал, метрики, обе реплики и запрет прямого egress. Результаты и журналы сохраняются как артефакты Actions. Зеленый статус относится только к соответствующему выполненному запуску.

```bash
python -m pytest -q
python -m scripts.build_manifests
python -m scripts.run_local --smoke
python -m scripts.smoke --cluster
```

Лимиты 4096 байт, 3 запроса, окно 10 с и блокировка 5 с заданы для воспроизводимой проверки, а не подобраны для реального предприятия. Низкоскоростная утечка через разрешенный адрес в пределах лимитов остается возможной. Content-Type — заявленный метаданный, а не доказательство содержания тела. Контроль DNS-каналов, вредоносного ответа пользователю и производственная отказоустойчивость PostgreSQL выходят за пределы этого прототипа. Содержимое полезной нагрузки не записывается в журнал. Мониторинг хранится в emptyDir; БД — в PVC. Раздел о нагрузочных испытаниях должен опираться на отдельно полученные измерения.

## Документация используемых технологий

* [NGINX auth_request](https://nginx.org/en/docs/http/ngx_http_auth_request_module.html)
* [NGINX HTTPS upstream](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_ssl_verify)
* [Kubernetes NetworkPolicy](https://kubernetes.io/docs/concepts/services-networking/network-policies/)
* [SQLAlchemy SELECT FOR UPDATE](https://docs.sqlalchemy.org/en/20/core/selectable.html#sqlalchemy.sql.expression.Select.with_for_update)
* [FastAPI JWT](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)
